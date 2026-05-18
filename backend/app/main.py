from fastapi import FastAPI

app = FastAPI(title="CatDV Annotator")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
