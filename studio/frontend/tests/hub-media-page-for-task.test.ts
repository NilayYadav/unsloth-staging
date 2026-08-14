// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

import assert from "node:assert/strict";
import test from "node:test";

import {
  mediaPageForTask,
  routedMediaPageForRow,
  studioPageForTask,
} from "../src/features/hub/lib/unsloth-support.ts";

test("media tasks route to the page that runs them", () => {
  assert.equal(mediaPageForTask("text-to-image"), "images");
  assert.equal(mediaPageForTask("image-text-to-video"), "video");
  assert.equal(mediaPageForTask("text-to-speech"), "audio");
  assert.equal(mediaPageForTask("automatic-speech-recognition"), "audio");
});

test("chat tasks and unknown tags stay with chat", () => {
  assert.equal(mediaPageForTask("text-generation"), undefined);
  assert.equal(mediaPageForTask(null), undefined);
  assert.equal(mediaPageForTask(""), undefined);
});

test("studioPageForTask stays free of audio so the chat pickers keep excluding it", () => {
  assert.equal(studioPageForTask("text-to-speech"), undefined);
  assert.equal(studioPageForTask("automatic-speech-recognition"), undefined);
  assert.equal(studioPageForTask("text-to-image"), "images");
});

// A filesystem row carries a real media task: the backend classifies a local diffusers
// checkpoint as text-to-image. Routing it is what must not happen, because the media pages
// resolve `model` as a Hub id, so Run would fall through to chat and evict the loaded model.
test("filesystem rows never count as running on a media page", () => {
  assert.equal(
    routedMediaPageForRow("text-to-image", "local", "models_dir"),
    undefined,
  );
  assert.equal(
    routedMediaPageForRow("text-to-video", "local", "custom"),
    undefined,
  );
  assert.equal(
    routedMediaPageForRow("text-to-speech", "local", "lmstudio"),
    undefined,
  );
  assert.equal(
    routedMediaPageForRow("text-to-image", "local", "ollama"),
    undefined,
  );
  assert.equal(
    routedMediaPageForRow("text-to-image", "local", undefined),
    undefined,
  );
});

test("hub-backed rows keep routing to the page that runs them", () => {
  assert.equal(
    routedMediaPageForRow("text-to-image", "local", "hf_cache"),
    "images",
  );
  assert.equal(routedMediaPageForRow("text-to-image", "cache"), "images");
  assert.equal(
    routedMediaPageForRow("image-text-to-video", "discover"),
    "video",
  );
  assert.equal(routedMediaPageForRow("text-to-speech", "cache"), "audio");
});

test("a non-media task has no media page whatever the row is", () => {
  assert.equal(routedMediaPageForRow("text-generation", "cache"), undefined);
  assert.equal(routedMediaPageForRow(null, "discover"), undefined);
  assert.equal(
    routedMediaPageForRow("text-generation", "local", "hf_cache"),
    undefined,
  );
});
