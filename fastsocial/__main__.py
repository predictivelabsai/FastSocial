import uvicorn

from fastsocial.config import settings

if __name__ == "__main__":
    uvicorn.run("fastsocial.app:app", host="0.0.0.0", port=settings().port, reload=False)
