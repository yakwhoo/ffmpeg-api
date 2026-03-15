from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess, os, tempfile, httpx, asyncio, uuid
from pathlib import Path

app = FastAPI(title="FFmpeg Merge API")

WORK_DIR = Path(tempfile.gettempdir()) / "ffmpeg_work"
WORK_DIR.mkdir(exist_ok=True)


class MergeRequest(BaseModel):
    video_urls: list[str]
    output_filename: str = "merged.mp4"
    upload_to_drive: bool = False
    drive_folder_id: str = ""


async def download_file(url: str, dest: Path):
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/merge")
async def merge_videos(req: MergeRequest):
    if len(req.video_urls) < 2:
        raise HTTPException(400, "video_urls must contain at least 2 URLs")

    job_id = str(uuid.uuid4())[:8]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir()

    try:
        clip_paths = []
        for i, url in enumerate(req.video_urls):
            dest = job_dir / f"clip_{i:02d}.mp4"
            await download_file(url, dest)
            clip_paths.append(dest)

        concat_list = job_dir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in clip_paths)
        )

        output_path = job_dir / req.output_filename
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            raise HTTPException(500, f"FFmpeg error: {result.stderr[-500:]}")

        file_size = output_path.stat().st_size

        return {
            "job_id": job_id,
            "output_filename": req.output_filename,
            "file_size_mb": round(file_size / 1024 / 1024, 2),
            "download_url": f"/download/{job_id}/{req.output_filename}",
            "clip_count": len(clip_paths)
        }

    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/download/{job_id}/{filename}")
async def download_result(job_id: str, filename: str):
    from fastapi.responses import FileResponse
    path = WORK_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="video/mp4", filename=filename)
