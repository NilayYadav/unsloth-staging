// SPDX-License-Identifier: AGPL-3.0-only
// Copyright 2026-present the Unsloth AI Inc. team. All rights reserved. See /studio/LICENSE.AGPL-3.0

import assert from "node:assert/strict";
import test from "node:test";

import {
  mediaPageForTask,
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
