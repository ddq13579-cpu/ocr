import json
import tempfile
from pathlib import Path

from alibabacloud_ocr_api20210707.client import Client as OCRClient
from alibabacloud_ocr_api20210707 import models as ocr_models
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models
from PIL import Image

from ...config import ALIBABA_CLOUD_ACCESS_KEY_ID, ALIBABA_CLOUD_ACCESS_KEY_SECRET


class AlibabaOCRProvider:
    def __init__(self):
        if not ALIBABA_CLOUD_ACCESS_KEY_ID or not ALIBABA_CLOUD_ACCESS_KEY_SECRET:
            raise ValueError(
                "ALIBABA_CLOUD_ACCESS_KEY_ID and ALIBABA_CLOUD_ACCESS_KEY_SECRET must be configured"
            )

        config = open_api_models.Config(
            access_key_id=ALIBABA_CLOUD_ACCESS_KEY_ID,
            access_key_secret=ALIBABA_CLOUD_ACCESS_KEY_SECRET,
        )
        config.endpoint = "ocr-api.cn-hangzhou.aliyuncs.com"
        self._client = OCRClient(config)
        self._runtime = util_models.RuntimeOptions(connect_timeout=10_000, read_timeout=60_000)

    def extract(self, path: Path) -> str:
        temporary_path: Path | None = None
        try:
            source_path = path
            if path.suffix.lower() == ".heic":
                with Image.open(path) as image:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporary_file:
                        temporary_path = Path(temporary_file.name)
                    image.save(temporary_path, format="PNG")
                source_path = temporary_path

            with source_path.open("rb") as image_file:
                response = self._client.recognize_general_with_options(
                    ocr_models.RecognizeGeneralRequest(body=image_file),
                    self._runtime,
                )
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)

        if not response.body:
            raise ValueError("Alibaba Cloud OCR returned an empty response")
        if response.body.code and response.body.code != "200":
            raise ValueError(f"Alibaba Cloud OCR failed: {response.body.code}: {response.body.message}")
        if not response.body.data:
            return ""

        data = json.loads(response.body.data)
        lines = [
            str(word_info["word"]).strip()
            for word_info in data.get("prism_wordsInfo", [])
            if word_info.get("word")
        ]
        return "\n".join(lines).strip() or str(data.get("content", "")).strip()
