"""Short-lived, single-use image challenges for account forms."""

import base64
import secrets
from datetime import datetime, timedelta
from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .auth import token_digest
from .models import CaptchaChallenge

CAPTCHA_SECONDS = 180
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_random = secrets.SystemRandom()


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(5))


def _image_data(code: str) -> str:
    width, height = 156, 52
    image = Image.new("RGB", (width, height), "#edf2fb")
    draw = ImageDraw.Draw(image)
    for _ in range(105):
        draw.point((_random.randrange(width), _random.randrange(height)), fill="#91a5c4")

    font = ImageFont.load_default(size=30)
    for index, letter in enumerate(code):
        tile = Image.new("RGBA", (34, 42))
        ImageDraw.Draw(tile).text((17, 21), letter, font=font, anchor="mm", fill="#243b72")
        tile = tile.rotate(_random.randint(-22, 22), resample=Image.Resampling.BICUBIC)
        image.paste(tile, (5 + index * 28, _random.randint(3, 8)), tile)

    for _ in range(3):
        draw.line([(0, _random.randrange(height)), (width, _random.randrange(height))],
                  fill="#809ac3", width=1)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def issue_challenge(db: Session, purpose: str) -> dict:
    code = new_code()
    challenge_id = secrets.token_hex(16)
    now = datetime.utcnow()
    image = _image_data(code)
    db.execute(delete(CaptchaChallenge).where(CaptchaChallenge.expires_at <= now))
    db.add(CaptchaChallenge(
        id=challenge_id,
        purpose=purpose,
        answer_hash=token_digest(f"{challenge_id}:{code}"),
        expires_at=now + timedelta(seconds=CAPTCHA_SECONDS),
    ))
    db.commit()
    return {"captcha_id": challenge_id, "image": image, "expires_in": CAPTCHA_SECONDS}


def consume_challenge(db: Session, challenge_id: str, code: str, purpose: str) -> None:
    # Delete in a single statement so concurrent requests cannot reuse a code.
    digest = db.execute(
        delete(CaptchaChallenge)
        .where(CaptchaChallenge.id == challenge_id,
               CaptchaChallenge.purpose == purpose,
               CaptchaChallenge.expires_at > datetime.utcnow())
        .returning(CaptchaChallenge.answer_hash)
    ).scalar_one_or_none()
    db.commit()
    expected = token_digest(f"{challenge_id}:{code.upper()}")
    if digest is None or not secrets.compare_digest(digest, expected):
        raise HTTPException(400, "图片验证码错误或已失效，请换一张")
