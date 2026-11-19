import uvicorn

from gateway.app import PORT

if __name__ == "__main__":
    uvicorn.run("gateway.app:app", host="127.0.0.1", port=PORT)
