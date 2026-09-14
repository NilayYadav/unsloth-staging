# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/
"""Scene: does the model still follow the instruction you typed with a screenshot?

The defect is in the backend's own compaction, so the evidence is what the model answers
after one. A thread is opened with a picture and a standing instruction typed beside it,
padded past the window, and then asked an ordinary question. The compaction fires, the
evicted turns are distilled into the carried-forward block, and the reply either obeys the
standing instruction or does not.

Two readings, on the same resident model on both sides:

  probe   the whole history posted straight at the photographed Studio's own
          /v1/chat/completions, greedy and capped, so the answer is a readout of the
          prompt the backend built rather than of sampling luck
  shot    the same thread in the real chat UI, with the compaction notice the frontend
          renders and the reply underneath it

The instruction is a nonsense token so no model can produce it by chance: it is in the
reply only if the backend carried it through the reset.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import sys
import uuid
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

WORKSPACE = Path(os.environ.get("UNSLOTH_WORKSPACE", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from pr_ui_scenes._common import Session, api_get, api_post  # noqa: E402
from studio_test_kit.auth import seed_init_script  # noqa: E402
from studio_test_kit.ui import open_chat  # noqa: E402

# A fresh id per driver run: a thread left in a reused home would 409 on create and
# then be photographed with the PREVIOUS run's messages still in it.
THREAD_ID = f"uidiff-compaction-10165-{uuid.uuid4().hex[:8]}"
CREATED_AT = 1_755_000_000_000

TOKEN = "BLUEBERRY-7788"
INSTRUCTION = (
    f"Standing instruction for the rest of this task: end every single reply with the "
    f"exact word {TOKEN} on its own final line, whatever I ask."
)
# Deliberately ON the padding's topic. The archive recall runs on every checkpoint
# reset and retrieves against THIS text; a question unrelated to everything lets the
# lone distinctive chunk -- the standing instruction -- win retrieval and be handed
# back, which masks whether the BLOCK carried it. A question the padding answers
# spends the recall slots on padding, leaving the block as the only carrier.
QUESTION = "How many crates did we audit at the loading dock? One short sentence."
# Enough to overflow a 4096-token window several times over, so the reset is not a
# near thing that a slightly different tokenizer could undo.
# Short turns, and many of them. A padding turn has to cost much LESS than the
# block's whole budget, or only one item fits and the newest-first walk takes it
# whatever the opening is priced at -- which tests the walk, not the pricing.
SECTIONS = 110
SECTION_CHARS = 190
# A screenshot's SIZE is the whole defect: the turn used to be priced on the payload it
# carries rather than on the words that reach the block. 256x256 of noise is a quarter of
# a megabyte of base64 -- an ordinary screenshot -- and incompressible, so the estimator
# cannot shrink it. The model still spends its fixed per-image token budget on it, so the
# real context cost stays small while the ESTIMATE is enormous. That gap is the bug.
IMAGE_SIDE = 256


def _noise_png(side: int = IMAGE_SIDE) -> str:
    """A real PNG that does not compress, so it costs what a screenshot costs."""
    import random

    rng = random.Random(10165)
    raw = b"".join(
        b"\x00" + bytes(rng.randrange(256) for _ in range(side * 3)) for _ in range(side)
    )

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


IMAGE_URL = _noise_png()


_TOPICS = (
    "loading dock", "cold store", "spare parts cage", "returns bay", "packing bench",
    "pallet yard", "quarantine shelf", "tool crib", "paint locker", "goods-in desk",
)
_VERBS = ("audited", "re-labelled", "weighed", "photographed", "re-binned", "counted",
          "sealed", "re-stacked", "sampled", "logged")
_NOUNS = ("crates", "drums", "reels", "cartons", "sacks", "trays", "spools", "kegs",
          "bundles", "canisters")


def _section_text(index: int) -> str:
    """Padding that is genuinely DISTINCT per turn.

    Near-identical padding collapses in the archive -- turns are deduplicated by content
    hash and repeated ones are budgeted -- which leaves the standing instruction as almost
    the only distinct chunk in the store, so the recall hands it back on any query and the
    reply cannot tell you whether the BLOCK carried it.
    """
    topic = _TOPICS[index % len(_TOPICS)]
    verb = _VERBS[(index * 3) % len(_VERBS)]
    noun = _NOUNS[(index * 7) % len(_NOUNS)]
    body = (
        f"Section {index}: at the {topic} we {verb} {17 + index} {noun} against docket "
        f"D-{4000 + index * 13}, found {index % 5} short, and moved the remainder to aisle "
        f"{chr(65 + index % 20)}{index % 40}. "
    )
    while len(body) < SECTION_CHARS:
        body += (
            f"Bay {index % 12} held {noun} from lot L-{9000 - index * 7}; "
            f"the {topic} tally now reads {123 + index * 3}. "
        )
    return body[:SECTION_CHARS]


def _history() -> list[dict]:
    """The thread: a picture with an instruction typed beside it, then a long chat."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": INSTRUCTION},
            {"type": "image_url", "image_url": {"url": IMAGE_URL}},
        ],
    }, {"role": "assistant", "content": "Understood."}]
    for index in range(SECTIONS):
        messages += [
            {"role": "user", "content": _section_text(index)},
            {"role": "assistant", "content": f"Section {index} noted."},
        ]
    return messages + [{"role": "user", "content": QUESTION}]


def _load_model(session: Session, model_path: str, context_length: int,
                timeout_s: int = 1800) -> dict:
    api_post(session, "/api/inference/load",
             {"model_path": model_path, "max_seq_length": context_length}, timeout=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = api_get(session, "/api/inference/status")
        if status.get("active_model") and not status.get("loading"):
            return status
        time.sleep(3)
    raise RuntimeError(f"{model_path} never became resident")


def _probe(session: Session, model_id: str) -> tuple[int, dict]:
    """The whole history at the photographed server, greedy so the reply is the prompt.

    ``thread_id`` and ``context_overflow`` are both REQUIRED to reach the code under test.
    `_can_reset_epoch` refuses a threadless request outright -- "API-only and threadless
    requests archive nothing, so they keep the rolling window" -- and the fit only runs at
    all under `context_overflow == "truncate_oldest"`. Without either, the server answers
    413-shaped "Message too long" and no compaction happens.

    ``max_tokens`` is generous because this model reasons before it answers: at 64 the
    whole budget went to the thinking block and `content` came back empty, which reads as
    "the model ignored the instruction" when it simply never got to reply.
    """
    payload = {
        "model": model_id,
        "messages": _history(),
        "thread_id": THREAD_ID,
        "context_overflow": "truncate_oldest",
        "max_tokens": 900,
        "temperature": 0,
        "top_p": 1,
        "seed": 7,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{session.base_url}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {"__error__": exc.read().decode()[:2000]}


def _put(session: Session, path: str, payload: dict) -> None:
    req = urllib.request.Request(
        f"{session.base_url}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {session.access_token}"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=120):
        pass


def _seed_thread(session: Session, model_id: str) -> None:
    """The same history in the store, so the UI shows the thread the probe measured."""
    api_post(session, "/api/chat/threads", {
        "id": THREAD_ID, "title": "Screenshot, then a long chat", "modelType": "base",
        "modelId": model_id, "archived": False,
        "createdAt": CREATED_AT, "updatedAt": CREATED_AT,
    })
    stamp = CREATED_AT
    for index, message in enumerate(_history()[:-1]):
        stamp += 1
        content = message["content"]
        if isinstance(content, str):
            parts = [{"type": "text", "text": content}]
        else:
            parts = [
                {"type": "text", "text": part["text"]} if part["type"] == "text"
                else {"type": "image", "image": part["image_url"]["url"]}
                for part in content
            ]
        _put(session, f"/api/chat/threads/{THREAD_ID}/messages/{THREAD_ID}-{index}", {
            "id": f"{THREAD_ID}-{index}", "threadId": THREAD_ID,
            "role": message["role"], "content": parts, "createdAt": stamp,
        })


async def drive(session: Session, out_dir: Path, label: str, *, model_path: str = "",
                context_length: int = 4096, **_: object) -> tuple[list[Path], dict]:
    loaded = _load_model(session, model_path, context_length)
    model_id = loaded.get("active_model") or model_path

    # FIRST: `can_archive` reads studio.db, so the thread must already hold its messages
    # when the completion arrives, or the reset is refused and nothing compacts.
    _seed_thread(session, model_id)

    status_code, body = _probe(session, model_id)
    choices = body.get("choices") or [{}]
    reply = ((choices[0].get("message") or {}).get("content") or "").strip()
    finish = choices[0].get("finish_reason")
    truncation = body.get("context_truncated") or {}
    compacted = bool(truncation)
    # The whole scene is about what survives a reset. A thread that FIT proves nothing:
    # the instruction is then still in the history verbatim and both sides answer with it,
    # which photographs as "the PR changed nothing". Fail loudly instead.
    if status_code != 200 or not compacted:
        raise RuntimeError(
            f"[{label}] no compaction happened (status {status_code}, "
            f"context_truncated={truncation!r}); the thread fit the window, so this pair "
            "would prove nothing. Lengthen the padding or shrink context_length."
        )

    auth_script = seed_init_script(
        type("A", (), {"access_token": session.access_token,
                       "refresh_token": session.refresh_token})(), [],
    )

    shots: list[Path] = []
    ui_reply = ""
    compaction_notice = False
    async with open_chat(session.base_url, init_scripts=[auth_script],
                         viewport=(1280, 900), headless=True) as sp:
        page = sp.page
        await page.goto(f"{session.base_url}/chat?thread={THREAD_ID}",
                        wait_until="domcontentloaded", timeout=90_000)
        composer = page.locator("form:has(textarea) textarea").first
        await composer.wait_for(state="visible", timeout=90_000)
        await page.wait_for_timeout(4_000)

        await composer.click()
        await composer.fill(QUESTION)
        await page.wait_for_timeout(500)
        await composer.press("Enter")

        # Wait for the GENERATION to end, not for text to appear. This model reasons
        # first, so the thinking panel fills the transcript within a second or two; any
        # "is there text yet" condition is satisfied by that and photographs a half-written
        # turn with no reply in it, which is what the first run of this scene did.
        stop = page.get_by_label("Stop generating")
        for _ in range(30):                       # let the send actually start
            await page.wait_for_timeout(1_000)
            if await stop.count():
                break
        for _ in range(240):                      # then wait for it to finish
            if not await stop.count():
                break
            await page.wait_for_timeout(1_000)
        await page.wait_for_timeout(3_000)

        # Collapse the reasoning panel so the REPLY is what the picture shows.
        try:
            thinking = page.get_by_text("Thinking", exact=False).last
            if await thinking.count():
                await thinking.click(timeout=5_000)
                await page.wait_for_timeout(1_500)
        except Exception:  # noqa: BLE001 -- an uncollapsible panel is not a scene failure
            pass

        text = await page.locator("body").inner_text()
        compaction_notice = "was compacted" in text.lower()
        ui_reply = text.split(QUESTION[:12])[-1].strip()[:400] if QUESTION[:12] in text else ""

        await page.keyboard.press("End")
        await page.wait_for_timeout(1_500)
        shot = out_dir / f"{label.lower()}_01_reply_after_compaction.png"
        await page.screenshot(path=str(shot), full_page=False)
        shots.append(shot)

    facts = {
        "model": model_id,
        "context_length": context_length,
        "history_messages": len(_history()),
        "image_payload_chars": len(IMAGE_URL),
        "probe_status": status_code,
        "probe_compacted": compacted,
        "probe_checkpoint_started": truncation.get("checkpoint_started"),
        "probe_dropped_messages": truncation.get("dropped_messages"),
        "probe_finish_reason": finish,
        "probe_reply": reply,
        "probe_followed_standing_instruction": TOKEN in reply,
        "ui_compaction_notice_shown": compaction_notice,
        "ui_reply_tail": ui_reply,
        "ui_followed_standing_instruction": TOKEN in ui_reply,
        "instruction_token": TOKEN,
    }
    return shots, facts
