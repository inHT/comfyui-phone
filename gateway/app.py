import asyncio
import json
import math
import mimetypes
import random
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import websockets
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"
IMAGES = DATA / "images"
JOBS_FILE = DATA / "jobs.json"
SESSIONS_FILE = DATA / "sessions.json"
TUNNEL_FILE = ROOT / "内网穿透隧道信息.txt"
COMFY = "http://127.0.0.1:8188"
CLIENT_ID = secrets.token_hex(8)

CLIP_NAME = "qwen3vl_4b_fp8_scaled.safetensors"
CLIP_TYPE = "krea2"
VAE_NAME = "qwen_image_vae.safetensors"
MEGAPIXELS = 1.0
MULTIPLE = 8

ASPECTS = [
    ("1:1 (Square)", "1:1 正方", 1, 1),
    ("2:3 (Portrait Photo)", "2:3 竖幅", 2, 3),
    ("3:2 (Photo)", "3:2 横幅", 3, 2),
    ("3:4 (Portrait Standard)", "3:4 竖幅", 3, 4),
    ("4:3 (Standard)", "4:3 横幅", 4, 3),
    ("9:16 (Portrait Widescreen)", "9:16 竖屏", 9, 16),
    ("16:9 (Widescreen)", "16:9 横屏", 16, 9),
    ("21:9 (Ultrawide)", "21:9 超宽", 21, 9),
]


def pixels(w_ratio: int, h_ratio: int) -> tuple[int, int]:
    total = MEGAPIXELS * 1024 * 1024
    scale = math.sqrt(total / (w_ratio * h_ratio))
    width = round(w_ratio * scale / MULTIPLE) * MULTIPLE
    height = round(h_ratio * scale / MULTIPLE) * MULTIPLE
    return width, height


def read_tunnel() -> tuple[int, str, str]:
    port = 8787
    password = ""
    public = ""
    text = TUNNEL_FILE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "：" not in line:
            continue
        key, value = line.split("：", 1)
        value = value.strip()
        if key.strip() == "端口":
            port = int(value)
        elif key.strip() == "访问密码":
            password = value
        elif key.strip() == "隧道":
            public = value
    if not password:
        raise RuntimeError("隧道信息里没有访问密码")
    return port, password, public


PORT, PASSWORD, PUBLIC_HOST = read_tunnel()

jobs: dict[str, dict] = {}
sessions: set[str] = set()
by_prompt: dict[str, str] = {}
state_lock = asyncio.Lock()
option_cache: dict = {"at": 0.0, "data": None}
client: httpx.AsyncClient | None = None
bearer = HTTPBearer(auto_error=False)


def public_job(job: dict) -> dict:
    return {key: value for key, value in job.items() if not key.startswith("_")}


def persist() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)
    ordered = sorted(jobs.values(), key=lambda item: item["created_at"], reverse=True)
    JOBS_FILE.write_text(
        json.dumps([public_job(item) for item in ordered], ensure_ascii=False),
        encoding="utf-8",
    )
    SESSIONS_FILE.write_text(json.dumps(sorted(sessions)), encoding="utf-8")


def load_state() -> None:
    if JOBS_FILE.exists():
        for item in json.loads(JOBS_FILE.read_text(encoding="utf-8")):
            if item.get("status") in {"queued", "running"}:
                item["status"] = "error"
                item["error"] = "网关重启时这张图还没跑完"
            jobs[item["id"]] = item
            if item.get("prompt_id"):
                by_prompt[item["prompt_id"]] = item["id"]
    if SESSIONS_FILE.exists():
        sessions.update(json.loads(SESSIONS_FILE.read_text(encoding="utf-8")))


def trim_jobs() -> None:
    ordered = sorted(jobs.values(), key=lambda item: item["created_at"])
    while len(ordered) > 80:
        old = ordered.pop(0)
        jobs.pop(old["id"], None)
        if old.get("prompt_id"):
            by_prompt.pop(old["prompt_id"], None)
        if old.get("file"):
            path = IMAGES / old["file"]
            if path.exists():
                path.unlink()


async def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    token = credentials.credentials if credentials else ""
    if token not in sessions:
        raise HTTPException(status_code=401, detail="请先登录")
    return token


def stack_loras(graph: dict, loras: list[dict]) -> tuple[list, list]:
    model = ["4", 0]
    clip = ["11", 0]
    if not loras:
        return model, clip
    previous = None
    last_id = ""
    for index in range(0, len(loras), 3):
        chunk = loras[index : index + 3]
        node_id = f"lora{index // 3}"
        inputs = {}
        if previous:
            inputs["lora_stack"] = [previous, 0]
        for slot in range(3):
            number = slot + 1
            if slot < len(chunk):
                item = chunk[slot]
                inputs[f"switch_{number}"] = "On"
                inputs[f"lora_name_{number}"] = item["name"]
                inputs[f"model_weight_{number}"] = item["model_weight"]
                inputs[f"clip_weight_{number}"] = item["clip_weight"]
            else:
                inputs[f"switch_{number}"] = "Off"
                inputs[f"lora_name_{number}"] = "None"
                inputs[f"model_weight_{number}"] = 0
                inputs[f"clip_weight_{number}"] = 0
        graph[node_id] = {"class_type": "CR LoRA Stack", "inputs": inputs}
        previous = node_id
        last_id = node_id
    graph["79"] = {
        "class_type": "CR Apply LoRA Stack",
        "inputs": {
            "model": ["4", 0],
            "clip": ["11", 0],
            "lora_stack": [last_id, 0],
        },
    }
    return ["79", 0], ["79", 1]


def build_workflow(params: dict) -> dict:
    graph = {
        "4": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": params["unet"], "weight_dtype": "default"},
        },
        "11": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP_NAME, "type": CLIP_TYPE},
        },
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_NAME}},
        "49": {
            "class_type": "ResolutionSelector",
            "inputs": {
                "aspect_ratio": params["aspect_ratio"],
                "megapixels": MEGAPIXELS,
                "multiple": MULTIPLE,
            },
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": ["49", 0],
                "height": ["49", 1],
                "batch_size": 1,
            },
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": params["prompt"], "clip": ["11", 0]},
        },
        "13": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["6", 0]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["12", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "phone", "images": ["8", 0]},
        },
    }
    model, clip = stack_loras(graph, params["loras"])
    graph["6"]["inputs"]["clip"] = clip
    graph["3"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": model,
            "positive": ["6", 0],
            "negative": ["13", 0],
            "latent_image": ["5", 0],
            "seed": params["seed"],
            "steps": params["steps"],
            "cfg": params["cfg"],
            "sampler_name": params["sampler_name"],
            "scheduler": params["scheduler"],
            "denoise": 1,
        },
    }
    return graph


async def comfy_json(method: str, path: str, **kwargs):
    assert client is not None
    response = await client.request(method, f"{COMFY}{path}", **kwargs)
    if response.status_code >= 400:
        detail = response.text[:500]
        raise HTTPException(status_code=502, detail=f"ComfyUI 拒绝了请求：{detail}")
    return response.json()


async def list_options() -> dict:
    now = time.time()
    if option_cache["data"] and now - option_cache["at"] < 10:
        return option_cache["data"]
    try:
        unets, loras, sampler_info = await asyncio.gather(
            comfy_json("GET", "/models/diffusion_models"),
            comfy_json("GET", "/models/loras"),
            comfy_json("GET", "/object_info/KSampler"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="连不上电脑上的 ComfyUI") from exc
    required = sampler_info["KSampler"]["input"]["required"]
    data = {
        "unets": unets,
        "loras": loras,
        "samplers": required["sampler_name"][0],
        "schedulers": required["scheduler"][0],
        "aspects": [
            {
                "id": key,
                "label": label,
                "width": pixels(w_ratio, h_ratio)[0],
                "height": pixels(w_ratio, h_ratio)[1],
            }
            for key, label, w_ratio, h_ratio in ASPECTS
        ],
        "defaults": {
            "steps": 8,
            "cfg": 1,
            "sampler_name": "euler",
            "scheduler": "simple",
            "aspect_ratio": "1:1 (Square)",
        },
    }
    option_cache["at"] = now
    option_cache["data"] = data
    return data


class LoginIn(BaseModel):
    password: str


class LoraSlot(BaseModel):
    enabled: bool = False
    name: str = ""
    model_weight: float = Field(default=0.8, ge=-10, le=10)
    clip_weight: float = Field(default=0.8, ge=-10, le=10)


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    unet: str
    aspect_ratio: str
    steps: int = Field(ge=1, le=40)
    cfg: float = Field(ge=1, le=12)
    sampler_name: str
    scheduler: str
    seed: int | None = None
    loras: list[LoraSlot] = Field(default_factory=list, max_length=9)


def find_job(prompt_id: str | None) -> dict | None:
    if prompt_id and prompt_id in by_prompt:
        return jobs.get(by_prompt[prompt_id])
    running = [item for item in jobs.values() if item["status"] in {"queued", "running"}]
    if len(running) == 1:
        return running[0]
    return None


async def store_image(job: dict, filename: str, subfolder: str, folder_type: str) -> None:
    async with state_lock:
        current = jobs.get(job["id"])
        if current is None or current.get("file") or current.get("_saving"):
            return
        current["_saving"] = True
    assert client is not None
    response = await client.get(
        f"{COMFY}/view",
        params={"filename": filename, "subfolder": subfolder, "type": folder_type},
    )
    response.raise_for_status()
    ext = Path(filename).suffix or ".png"
    name = f"{job['id']}{ext}"
    IMAGES.mkdir(parents=True, exist_ok=True)
    (IMAGES / name).write_bytes(response.content)
    async with state_lock:
        current = jobs[job["id"]]
        current["file"] = name
        current["status"] = "done"
        current["progress"] = 1
        current["error"] = None
        current.pop("_saving", None)
        persist()


async def pull_from_history(prompt_id: str) -> None:
    assert client is not None
    response = await client.get(f"{COMFY}/history/{prompt_id}")
    if response.status_code != 200:
        return
    record = response.json().get(prompt_id) or {}
    outputs = record.get("outputs") or {}
    for node in outputs.values():
        images = node.get("images") or []
        if images:
            image = images[0]
            job = find_job(prompt_id)
            if job:
                await store_image(
                    job,
                    image.get("filename", ""),
                    image.get("subfolder", ""),
                    image.get("type", "output"),
                )
            return
    status = (record.get("status") or {}).get("status_str")
    if status == "error":
        messages = (record.get("status") or {}).get("messages") or []
        text = "出图失败"
        for item in messages:
            if isinstance(item, list) and item and item[0] == "execution_error":
                text = (item[1] or {}).get("exception_message") or text
        async with state_lock:
            job = find_job(prompt_id)
            if job and job["status"] != "done":
                job["status"] = "error"
                job["error"] = str(text)[:500]
                persist()


async def on_event(message: dict) -> None:
    kind = message.get("type")
    data = message.get("data") or {}
    prompt_id = data.get("prompt_id")
    if kind == "execution_start":
        async with state_lock:
            job = find_job(prompt_id)
            if job:
                job["status"] = "running"
                persist()
    elif kind == "progress":
        value = data.get("value") or 0
        maximum = data.get("max") or 1
        async with state_lock:
            job = find_job(prompt_id)
            if job and job["status"] != "done":
                job["status"] = "running"
                job["step"] = value
                job["step_max"] = maximum
                job["progress"] = 0.12 + 0.8 * (value / maximum)
                persist()
    elif kind == "execution_error":
        async with state_lock:
            job = find_job(prompt_id)
            if job and job["status"] != "done":
                job["status"] = "error"
                job["error"] = str(data.get("exception_message") or "出图失败")[:500]
                persist()
    elif kind == "execution_success":
        if prompt_id:
            await pull_from_history(prompt_id)
    elif kind == "executed":
        images = (data.get("output") or {}).get("images") or []
        if images and prompt_id:
            image = images[0]
            job = find_job(prompt_id)
            if job:
                await store_image(
                    job,
                    image.get("filename", ""),
                    image.get("subfolder", ""),
                    image.get("type", "output"),
                )


async def watch_comfy() -> None:
    while True:
        try:
            async with websockets.connect(
                f"ws://127.0.0.1:8188/ws?clientId={CLIENT_ID}",
                open_timeout=5,
                ping_interval=20,
            ) as socket:
                async for raw in socket:
                    if not isinstance(raw, str):
                        continue
                    await on_event(json.loads(raw))
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    load_state()
    DATA.mkdir(parents=True, exist_ok=True)
    client = httpx.AsyncClient(timeout=120)
    watcher = asyncio.create_task(watch_comfy())
    host = f"https://{PUBLIC_HOST}" if PUBLIC_HOST else "(隧道地址未知)"
    print(f"网关监听 127.0.0.1:{PORT}")
    print(f"手机打开 {host}")
    yield
    watcher.cancel()
    await client.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/icon-192.png")
async def icon192():
    return FileResponse(WEB / "icon-192.png")


@app.get("/icon-512.png")
async def icon512():
    return FileResponse(WEB / "icon-512.png")


@app.get("/api/health")
async def health():
    comfy = False
    if client is not None:
        try:
            response = await client.get(f"{COMFY}/system_stats", timeout=3)
            comfy = response.status_code == 200
        except Exception:
            comfy = False
    return {"ok": True, "comfy": comfy}


@app.post("/api/login")
async def login(body: LoginIn):
    if not secrets.compare_digest(body.password.encode(), PASSWORD.encode()):
        raise HTTPException(status_code=401, detail="密码不对")
    token = secrets.token_urlsafe(24)
    async with state_lock:
        sessions.add(token)
        persist()
    return {"token": token}


@app.get("/api/options")
async def options(_: str = Depends(require_user)):
    return await list_options()


@app.post("/api/generate")
async def generate(body: GenerateIn, _: str = Depends(require_user)):
    choices = await list_options()
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="提示词是空的")
    if body.unet not in choices["unets"]:
        raise HTTPException(status_code=400, detail="这个底模不在电脑上")
    if body.aspect_ratio not in {item["id"] for item in choices["aspects"]}:
        raise HTTPException(status_code=400, detail="分辨率不在可选范围内")
    if body.sampler_name not in choices["samplers"]:
        raise HTTPException(status_code=400, detail="采样器不存在")
    if body.scheduler not in choices["schedulers"]:
        raise HTTPException(status_code=400, detail="调度器不存在")
    loras = []
    for slot in body.loras:
        if not slot.enabled:
            continue
        if slot.name not in choices["loras"]:
            raise HTTPException(status_code=400, detail="有一个开启的 LoRA 不在电脑上")
        loras.append(
            {
                "enabled": True,
                "name": slot.name,
                "model_weight": slot.model_weight,
                "clip_weight": slot.clip_weight,
            }
        )
    aspect = next(item for item in choices["aspects"] if item["id"] == body.aspect_ratio)
    seed = body.seed if body.seed is not None else random.randint(0, 2**31 - 1)
    params = {
        "prompt": prompt,
        "unet": body.unet,
        "aspect_ratio": body.aspect_ratio,
        "aspect_label": aspect["label"],
        "width": aspect["width"],
        "height": aspect["height"],
        "steps": body.steps,
        "cfg": body.cfg,
        "sampler_name": body.sampler_name,
        "scheduler": body.scheduler,
        "seed": seed,
        "loras": loras,
    }
    workflow = build_workflow(params)
    try:
        result = await comfy_json(
            "POST",
            "/prompt",
            json={"prompt": workflow, "client_id": CLIENT_ID},
        )
    except HTTPException as exc:
        raise HTTPException(status_code=502, detail=str(exc.detail)) from exc
    if result.get("node_errors"):
        raise HTTPException(status_code=400, detail="工作流参数没有通过 ComfyUI 检查")
    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise HTTPException(status_code=502, detail="ComfyUI 没有收下这张图")
    job_id = secrets.token_hex(8)
    job = {
        "id": job_id,
        "prompt_id": prompt_id,
        "status": "queued",
        "progress": 0.02,
        "step": 0,
        "step_max": params["steps"],
        "error": None,
        "file": None,
        "created_at": time.time(),
        "params": params,
    }
    async with state_lock:
        jobs[job_id] = job
        by_prompt[prompt_id] = job_id
        trim_jobs()
        persist()
    return public_job(job)


@app.get("/api/jobs")
async def list_jobs(_: str = Depends(require_user)):
    ordered = sorted(jobs.values(), key=lambda item: item["created_at"], reverse=True)
    return [public_job(item) for item in ordered]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, _: str = Depends(require_user)):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="没有这张图")
    if job["status"] in {"queued", "running"} and job.get("prompt_id"):
        await pull_from_history(job["prompt_id"])
        job = jobs[job_id]
        if job["status"] in {"queued", "running"} and not await prompt_still_queued(job["prompt_id"]):
            async with state_lock:
                job["status"] = "error"
                job["error"] = "这张图已不在电脑的队列里"
                persist()
    return public_job(job)


async def prompt_still_queued(prompt_id: str) -> bool:
    assert client is not None
    try:
        response = await client.get(f"{COMFY}/queue", timeout=5)
    except Exception:
        return True
    if response.status_code != 200:
        return True
    data = response.json()
    for bucket in ("queue_running", "queue_pending"):
        for item in data.get(bucket) or []:
            if isinstance(item, list) and len(item) > 1 and item[1] == prompt_id:
                return True
    return False


@app.get("/api/jobs/{job_id}/image")
async def job_image(job_id: str, _: str = Depends(require_user)):
    job = jobs.get(job_id)
    if job is None or not job.get("file"):
        raise HTTPException(status_code=404, detail="图片还没好")
    path = IMAGES / job["file"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片文件不在了")
    media = mimetypes.guess_type(path.name)[0] or "image/png"
    return FileResponse(path, media_type=media)
