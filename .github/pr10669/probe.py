# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

"""Prints what /v1/messages actually reserves for one Anthropic image block."""

import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "studio", "backend"))

from models.inference import AnthropicMessagesRequest
from routes.inference import (
    _OPENAI_LLAMA_ADMISSION_IMAGE_TOKENS,
    _openai_llama_admission_tokens,
)
from core.inference.llama_admission import LlamaAdmissionConfig, LlamaAdmissionQueue

BUDGET = 32768
CAPACITY = 4
SHARE = BUDGET // CAPACITY
SCREENSHOT_KIB = 150


def image_b64(kib):
    return base64.b64encode(b"\x89PNG" + b"x" * (kib * 1024)).decode()


def request(data):
    return AnthropicMessagesRequest(
        model = "default",
        max_tokens = 128,
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": data,
                        },
                    },
                ],
            }
        ],
    )


def cost(data, budget):
    return _openai_llama_admission_tokens(request(data), budget = budget, capacity = CAPACITY)


async def concurrent_admissions():
    queue = LlamaAdmissionQueue("pr10669-probe")
    config = LlamaAdmissionConfig()
    leases = []
    for _ in range(CAPACITY):
        payload = request(image_b64(SCREENSHOT_KIB))
        reservation = queue.reserve(
            capacity = CAPACITY,
            config = config,
            budget = BUDGET,
            tokens = _openai_llama_admission_tokens(
                payload, budget = BUDGET, capacity = CAPACITY
            ),
        )
        leases.append(reservation.lease_nowait())
    admitted = sum(1 for lease in leases if lease is not None)
    for lease in leases:
        if lease is not None:
            lease.release()
    return admitted


screenshot_clamped = cost(image_b64(SCREENSHOT_KIB), BUDGET)
tiny_clamped = cost("AAAA", BUDGET)
screenshot_raw = cost(image_b64(SCREENSHOT_KIB), 1_000_000)
tiny_raw = cost("AAAA", 1_000_000)
admitted = asyncio.run(concurrent_admissions())

result = {
    "impl": sys.argv[1] if len(sys.argv) > 1 else "unknown",
    "kv_budget_tokens": BUDGET,
    "capacity_slots": CAPACITY,
    "equal_share_tokens": SHARE,
    "bounded_image_allowance_tokens": _OPENAI_LLAMA_ADMISSION_IMAGE_TOKENS,
    "screenshot_kib": SCREENSHOT_KIB,
    "screenshot_charged_tokens_unclamped": screenshot_raw,
    "tiny_image_charged_tokens_unclamped": tiny_raw,
    "screenshot_over_tiny_ratio": round(screenshot_raw / max(1, tiny_raw), 1),
    "screenshot_reservation_against_32k_cache": screenshot_clamped,
    "tiny_image_reservation_against_32k_cache": tiny_clamped,
    "screenshot_reserves_whole_cache": screenshot_clamped >= BUDGET,
    "screenshot_chats_admitted_together_out_of_4": admitted,
}
print("PROBE_JSON " + json.dumps(result))
print(json.dumps(result, indent = 2))
