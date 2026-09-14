# Adapted from private unslothai/scripts@494be796ead0d6bfe41a2b2f4d133fe013d4c62f: scripts/pr_ui_scenes/registry.py
"""Which scene photographs which PR, and what the shot is supposed to prove.

`expect` is the point of this file. A scene that runs cleanly and produces two
identical images is the default failure mode of this whole exercise -- a missed
click, the wrong dropdown, or a Studio that never rebuilt all look like "the PR
changed nothing". Writing down the expected difference BEFORE running turns that
silent failure into a checkable claim.

`needs_model` marks scenes that must load real weights. Those are slow and need a
GPU; the cheap ones (Hub listings only) should be developed first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ScenePlan:
    pr: int
    scene: str
    what: str                       # the UI surface photographed
    expect: str                     # the difference that MUST be visible
    kwargs: dict = field(default_factory=dict)
    needs_model: bool = False
    verified: Optional[str] = None  # what was actually observed, once run


REGISTRY: dict[int, ScenePlan] = {
    10941: ScenePlan(
        pr=10941, scene="sharegpt_branch_export",
        what="a prompt with two sibling replies (the second from Regenerate) seeded through the "
             "chat-history API, the branch picker clicked back to reply 1/2, then ShareGPT JSONL "
             "and Training JSONL downloaded from the composer's Export chat menu; the files' "
             "assistant turns are pinned beside the transcript. No model is loaded",
        expect="Controls on every side: stored_row_count 3, picker_state_on_load '2/2', "
               "picker_state_after_previous '1/2', on_screen_reply 'first'. BEFORE (main) "
               "sharegpt_gpt_turns ['regenerated','first'] (both replies, unmarked) and "
               "training_assistant_turns ['regenerated']. PR head ea8a41e before the review fix: "
               "sharegpt_gpt_turns ['regenerated'] -- only the reply the user navigated AWAY "
               "from -- and training the same. AFTER (fixed head): sharegpt_gpt_turns ['first'] "
               "and training_assistant_turns ['first'], sharegpt_matches_screen and "
               "training_matches_screen False -> True.",
    ),
    10934: ScenePlan(
        pr=10934, scene="top_p_off_gateway",
        what="a chat on a custom OpenAI-compatible connection (LiteLLM/Bedrock style) to "
             "claude-sonnet-4-6 with Run settings -> Top P dragged to Off, after sending one "
             "message. The connection's base URL points at a stand-in /v1/chat/completions "
             "held by the scene that rejects a body carrying both temperature and top_p with "
             "Bedrock's 400 message from issue #10917, and otherwise streams an answer",
        expect="BEFORE (merge base 5c04ec1930): Top P control reads 'Off' yet top_p_in_body "
               "true with top_p_sent 1.0 next to temperature, provider_statuses [400], the "
               "chat shows the 'cannot both be specified' error (error_visible true) and no "
               "assistant answer. AFTER (PR head): same control reading 'Off', top_p_in_body "
               "false, provider_statuses [200], the bubble shows the stand-in answer "
               "(assistant_answered true, error_visible false).",
    ),
    10814: ScenePlan(
        pr=10814, scene="research_code_report",
        what="the delivered Deep Research report in /chat, from a real ResearchSupervisor run "
             "against a saved custom connection served from the scene process. Both Studios "
             "load the same ddgs stub through PYTHONPATH, so the one research step gathers "
             "https://github.com/unslothai/unsloth on both sides, and synthesis returns the same "
             "report with fenced, indented and inline code on both sides.",
        expect="BEFORE (merge base 8ee07d6ae) the citation validator rewrites the code as prose: "
               "the bash block shows `pip install torch --index-url ` and `git clone "
               "[unslothai/unsloth](https://github.com/unslothai/unsloth)`, the python block "
               "shows `client = OpenAI(base_url=\", api_key=\"none\")` and `print(x.shape["
               "unslothai/unsloth](https://github.com/unslothai/unsloth))`. AFTER (head "
               "a7804082b) every code line renders exactly as written "
               "(code_lines_kept_in_report 1 -> 10, code_lines_kept_in_ui 1 -> 10). "
               "run_status completed, sources [github.com/unslothai/unsloth], "
               "prose_citation_linked true, unverified_prose_url_removed true and "
               "model_sources_section_removed true on BOTH sides.",
        verified="confirmed on base 8ee07d6ae vs head a7804082b, two isolated no-torch installs "
                 "in a Modal CPU container. code_lines_kept_in_report 1 -> 10 and "
                 "code_lines_kept_in_ui 1 -> 10. BEFORE rendered `git clone [unslothai/unsloth]"
                 "(https://github.com/unslothai/unsloth)`, `pip install torch --index-url`, "
                 "`client = OpenAI(base_url=\", api_key=\"none\")`, `print(x.shape[unslothai/"
                 "unsloth](https://github.com/unslothai/unsloth))`, `pattern = \"\"` and a bare "
                 "`wget`; AFTER rendered every line as written. run_status completed, sources "
                 "[https://github.com/unslothai/unsloth], ddgs_stub_calls 1, model_phases, "
                 "ui_code_blocks 3 and all prose facts IDENTICAL on both sides. The BEFORE clip "
                 "ends above the inline-code line.",
    ),
    10670: ScenePlan(
        pr=10670, scene="llama_update_banner_rate_limited",
        what="the llama.cpp update banner on /chat while api.github.com refuses every "
             "call with 403 and X-RateLimit-Remaining: 0. Both sides run the llama.cpp "
             "their own install.sh --local put in their own home (b10840-mix-d5c17a0 on "
             "both), and both load the same sitecustomize stub through PYTHONPATH, so "
             "GitHub is spent on BOTH sides and the release page redirect names b11000 "
             "on BOTH sides. Nothing is seeded and no weights or GPU are needed.",
        expect="BEFORE (merge base 1ad44677d) fetch_latest_release_tag has only the API "
               "to ask, so a spent quota answers nothing: latest_tag null, "
               "update_available false, banner_visible false, stub_redirects 0, and "
               "/chat carries no llama.cpp offer. AFTER (head) the lockout is recorded "
               "and the release page redirect answers instead: stub_redirects above 0, "
               "latest_tag b11000, update_available true, banner_visible true, and the "
               "banner names b11000. installed_tag must read b10840-mix-d5c17a0 on BOTH "
               "sides -- it is each home's own real install -- so a pair where it moved "
               "means the two installs are not equivalent. stub_api_403s must be "
               "non-zero on BOTH sides, or GitHub was reachable on one of them and the "
               "two are not comparable.",
        kwargs={"stub_log": "/Users/nilay/.mimir/skills/pr-ui-evidence/temp/ev10670/stub.log"},
        needs_model=False,
        verified="Ran BEFORE 1ad44677d vs AFTER b93d10ceb with PR10670_STUB_TAG=b11000. "
                 "BEFORE: latest_tag null, update_available false, banner_visible false, "
                 "stub_api_403s 4, stub_redirects 0, and /chat shows no banner. AFTER: "
                 "latest_tag b11000, update_available true, banner_visible true, "
                 "stub_api_403s 8, stub_redirects 3, and the banner reads 'New llama.cpp "
                 "update / b10840 -> b11000 / No restart needed after update'. The "
                 "control held: installed_tag read b10840 on both sides (the display tag "
                 "of the b10840-mix-d5c17a0 marker each home installed for itself), and "
                 "stub_api_403s was non-zero on both. The only incidental difference in "
                 "the composite is Studio's randomly rotated greeting line.",
    ),
    10668: ScenePlan(
        pr=10668, scene="tool_result_sentinel_anchor",
        what="the chat after an MCP tool returns a four-line build log whose second "
             "line quotes the literal text `__IMAGES__:` in the MIDDLE of a line (an "
             "uploader's source, as a build log would quote it), followed by the two "
             "lines the user actually asked for: the artifact URL and its sha256. A "
             "real stdio MCP server is spawned and called by the very Studio that is "
             "photographed; the stand-in provider answers with a sentence that is a "
             "pure function of the `role=tool` content it was handed.",
        expect="BEFORE (merge base fcaad20ef) `strip_result_for_model` truncates at the "
               "FIRST occurrence of the marker anywhere in the string: "
               "model_received_chars 43 of 155, chars_lost_before_model 112, "
               "model_received_artifact_url false, model_received_checksum false, and "
               "the bubble reads '... it stops after 2 line(s) and carries no artifact "
               "URL and no checksum'. AFTER (head c365d6660) only a structurally valid "
               "trailing envelope is stripped: model_received_chars 155, "
               "chars_lost_before_model 0, both flags true, and the bubble names the "
               "artifact and the sha256. The CONTROLS must hold on BOTH sides -- "
               "mcp_tool_returned_chars 155, card_shows_artifact_url true and "
               "card_shows_checksum true -- because the card renders the raw result "
               "and the frontend slices only a line-anchored marker. A pair where the "
               "card also moved would mean the frontend changed, not the stripper.",
        kwargs={},
        verified="Ran 2026-09-09 on two isolated installs, base fcaad20ef and "
                 "head c365d6660 (:9003), each stamped with its own SHA, same stdio MCP "
                 "server and same stand-in provider. Every predeclared key moved and no "
                 "other did: model_received_chars 43 -> 155, chars_lost_before_model "
                 "112 -> 0, model_received_lines 2 -> 4, model_received_artifact_url "
                 "false -> true, model_received_checksum false -> true, and "
                 "ui_answer_says_it_cannot true -> false; model_received_tail went from "
                 "the log cut mid-line at the quoted marker to the whole log. The "
                 "controls held on BOTH sides: mcp_tool_returned_chars 155, "
                 "card_shows_artifact_url true, card_shows_checksum true, "
                 "provider_completions 2, tool_offered_to_model true, and the two "
                 "card_texts byte-identical. The composite shows one identical tool "
                 "card on both halves listing all four log lines (artifact URL and "
                 "sha256 plainly visible) above two different bubbles: BEFORE 'From the "
                 "log I was handed: it stops after 2 line(s) and carries no artifact "
                 "URL and no checksum, so I cannot give you either.' vs AFTER 'From the "
                 "log I was handed: artifact https://builds.example.com/unsloth-42.tar.gz, "
                 "sha256 9f2c1ad4b7e60f38.' That is the PR in one picture: the card the "
                 "user reads is whole on both sides, so nothing looks wrong while the "
                 "model answers from a truncated log. Note ui_answer_gives_artifact_url "
                 "reads true on both sides because the transcript locator spans the card "
                 "as well as the bubble; ui_answer_says_it_cannot is the fact that "
                 "separates them. Re-shot after the follow-up commits: the AFTER home's SHA "
                 "stamp forced a rebuild at c365d6660 while BEFORE was reused at the "
                 "unchanged merge base, and every fact came back identical.",
    ),
    10667: ScenePlan(
        pr=10667, scene="rag_upload_unicode_name",
        what="the project Sources tab after four documents are uploaded through the real "
             "multipart route: two Chinese names that differ in every character, one name "
             "with an ordinary space, and one with accents. The panel prints doc.filename "
             "straight from /api/rag/projects/<id>/documents, so the rows on screen and "
             "the names in facts are the same server response. No model is loaded.",
        expect="BEFORE (merge base) the upload allowlist keeps only [A-Za-z0-9._-], so the "
               "list shows TWO rows both reading '_.txt' -- names_survived_verbatim false, "
               "two_chinese_names_collide true, distinct_server_names 3 for 4 documents -- "
               "plus 'My_Report.txt' and 'Caf_R_sum_.txt'. AFTER (head ba7c18a08) every row "
               "reads the name it was uploaded under: names_survived_verbatim true, "
               "two_chinese_names_collide false, distinct_server_names 4, and "
               "underscored_names empty. document_statuses must be the same on both sides, "
               "or ingestion moved and the pair is not comparable.",
        needs_model=False,
        verified="Exactly as expected, on base fcaad20ef vs head ba7c18a08, 2026-09-10. "
                 "BEFORE the Sources list reads 'Caf_R_sum_.txt', 'My_Report.txt', "
                 "'_.txt', '_.txt' -- four documents, three distinct names, the two "
                 "Chinese ones indistinguishable in both the composer chip row and the "
                 "'4 sources' list. AFTER it reads 'Cafe Resume.txt' (with its accents), "
                 "'My Report.txt', the two Chinese names in full. Facts moved as "
                 "predicted: distinct_server_names 3 -> 4, names_survived_verbatim "
                 "false -> true, two_chinese_names_collide true -> false, "
                 "underscored_names four entries -> empty. document_statuses is "
                 "['completed'] on BOTH sides, so ingestion did not move.",
    ),
    10552: ScenePlan(
        pr=10552, scene="rag_upload_stalls_stream",
        what="the chat transcript while a resident Qwen3-0.6B GGUF is streaming a long "
             "answer and a 180 MB document upload is fired at a knowledge base from the "
             "page itself, with the UI's own credentials over real HTTP. The measurement "
             "is the assistant text that arrives between the upload request leaving the "
             "browser and its response coming back -- the window in which the server was "
             "doing the copy, the sha256 re-read and the embedder probe.",
        expect="BEFORE (merge base 580cffaba) the three upload routes are async def and "
               "run ON the event loop, so the stream cannot advance while the upload is "
               "in flight: chars_streamed_during_upload is ~0 and the assistant message "
               "is visibly shorter in the shot. AFTER (head ca140769c) they are plain def "
               "and FastAPI runs them in the threadpool, so the reply keeps arriving "
               "throughout: chars_streamed_during_upload is well into the hundreds and "
               "the transcript is visibly longer. upload_status must be 200 on BOTH "
               "sides, or the upload never happened and nothing was measured.",
        kwargs={
            "model": "unsloth/Qwen3-0.6B-GGUF",
            "variant": "Q4_K_M",
            "context_length": 4096,
        },
        needs_model=True,
    ),
    10554: ScenePlan(
        pr=10554, scene="tool_result_error_prefix",
        what="the chat after the model has read two real public build logs through "
             "web_search: the first body is a SUCCESS line that merely opens with "
             "\"Errors: 0 across 128 files ...\", the second is a genuine "
             "\"Error: disk full ...\". Both are fetched for real over the network from "
             "httpbin's /base64 endpoint by the very Studio that was photographed, and "
             "the stand-in provider answers with a sentence that is a pure function of "
             "whether TOOL_ERROR_NUDGE was stapled to each result it was handed.",
        expect="BEFORE (merge base 580cffaba) the bare \"Error\" prefix matches both "
               "bodies, so BOTH results reach the model carrying the retry nudge: "
               "success_line_nudged_as_error true, real_failure_nudged_as_error true, "
               "n_nudged_as_errors 2, and the chat reads '... success_line as a FAILED "
               "call, and real_failure as a FAILED call.' AFTER (head 49aff1d1f) only "
               "the delimited form matches: success_line_nudged_as_error true -> false "
               "and n_nudged_as_errors 2 -> 1, while the control "
               "real_failure_nudged_as_error stays true on BOTH sides and both "
               "*_body_reached_the_model stay true on BOTH sides, so the fetch itself is "
               "held fixed. A pair where the real failure also flips would mean error "
               "detection broke rather than over-matching being fixed.",
        verified="Ran 2026-09-09 against base 580cffaba (:8991) and head 49aff1d1f "
                 "(:8990), two isolated installs, same two real httpbin bodies. Every "
                 "predeclared key moved and no other did: success_line_nudged_as_error "
                 "true -> false, n_nudged_as_errors 2 -> 1, results_nudged_as_errors "
                 "['success_line','real_failure'] -> ['real_failure'], "
                 "results_passed_through_clean [] -> ['success_line'], "
                 "success_line_result_len 163 -> 68 (exactly the 95 chars of "
                 "TOOL_ERROR_NUDGE), ui_answer_calls_success_line_failed true -> false. "
                 "The control held on BOTH sides: real_failure_nudged_as_error true, "
                 "real_failure_result_len 145, both *_body_reached_the_model true, "
                 "approvals_clicked 2, tool_cards_rendered 2, provider_completions 3, "
                 "tool_group_label '2 tool calls', and the two card_texts byte-identical. "
                 "The composite shows one sentence differing: BEFORE 'Studio handed me "
                 "success_line as a FAILED call, and real_failure as a FAILED call.' vs "
                 "AFTER '... success_line as a SUCCESSFUL call, and real_failure as a "
                 "FAILED call.' Note the tool CARDS are identical on both sides by "
                 "design -- tool_end_payload carries no is_error, so the nudge is the "
                 "only thing the backend's verdict changes, and it is visible through "
                 "what the model was handed rather than through card styling.",
    ),
    10558: ScenePlan(
        pr=10558, scene="export_size_one_copy",
        what="the Export page with a Llama 3.1 8B full-finetune run folder typed into the "
             "Local Model path and Merged Model selected, seeded under each Studio's own "
             "outputs root: four safetensors shards at Hub byte sizes beside optimizer.pt, "
             "scheduler.pt, training_args.bin, rng_state.pth and a checkpoint-100/ snapshot",
        expect="BEFORE (merge base) /api/models/export-size sums every weight-looking file, "
               "optimizer state included: api_fp16_bytes 80302667420 (74.79 GiB) and the "
               "page reads 'Est. size: ~74.8 GB'. AFTER (head) sizes one copy of the weights: "
               "api_fp16_bytes 16060556376 (14.96 GiB), 'Est. size: ~15 GB'. one_copy_bytes "
               "(16060556376) and files_on_disk_bytes (96363224655, checkpoint-100/ and "
               "optimizer.pt included) MUST read the same on both sides, or the seeded "
               "folders differ and the pair proves nothing.",
        kwargs={},
        verified="CONFIRMED by eye on two isolated installs, base cfa31f27b vs head 56aa2a350 (the loader-precedence rewrite plus the variant, same-stem, declared-library, nested-index and root-bookkeeping folds on top; identical on e4149b7bb, 35b98c25a, fb90d4981, 6aa5cac1d, c8510155a and on my own heads 49f30a247, 2127f9a71, 9ab62a5d4 and 44c9bea4e), "
                 "each stamped with its own SHA. files_on_disk_bytes 96363224655 and "
                 "one_copy_bytes 16060556376 identical on both sides. BEFORE api_fp16_bytes "
                 "80302667420 (74.79 GiB), page 'Est. size: ~75 GB'; AFTER api_fp16_bytes "
                 "16060556376 (14.96 GiB), page 'Est. size: ~15 GB'. Only the four predicted "
                 "keys moved (api_fp16_bytes, api_fp16_gib, ui_est_size_text, ui_est_size_value) "
                 "plus model_dir, which differs by home. The page rounds with 1024-based units "
                 "and toFixed(0) above 10, so 74.79 GiB reads '~75 GB', not '~74.8 GB'. "
                 "Scene also smoke-tested first against an older built home (a Sep 7 main), "
                 "which read '~75 GB' as well.",
    ),
    10553: ScenePlan(
        pr=10553, scene="image_transform_exif_orientation",
        what="the Images viewer after a Transform (img2img) run whose upload is a phone photo "
             "-- 768x448 pixels stored landscape, EXIF Orientation=6 saying show it rotated -- "
             "with a Z-Image-Turbo GGUF loaded, a 1024x1024 Resolution box and strength 0.30 so "
             "the four-quadrant colour card survives the denoise",
        expect="The upload is 768x448 on the wire and the browser shows it 448x768. BEFORE the "
               "decoder reads the raw pixels, so the img2img source is landscape and the run's "
               "OUTPUT is landscape with it: gallery_recorded_size [768, 448], "
               "result_pixel_size [768, 448], result_is_portrait False, result_quadrants "
               "['red','green','blue','yellow'] (the STORED order, not the one the user saw), "
               "result_matches_browser_truth False. AFTER the tag is applied: "
               "gallery_recorded_size [448, 768], result_pixel_size [448, 768], "
               "result_is_portrait True, result_quadrants ['blue','red','yellow','green'] == "
               "browser_truth_quadrants, result_matches_browser_truth True. The quadrant read is "
               "mirror-sensitive on purpose: orientation 5 is the diagonal flip of 6 and would "
               "give the same sizes, so sizes alone cannot tell a rotation from a mirror. The "
               "viewer shot shows a wide sideways card BEFORE and an upright tall one AFTER.",
        kwargs={"repo": "unsloth/Z-Image-Turbo-GGUF",
                "filename": "z-image-turbo-Q4_K_S.gguf"},
        needs_model=True,
        verified="confirmed on base 580cffaba (this PR's own merge base) vs head 0c2cb4efe, "
                 "both installed on ONE Modal A10G. Every predicted fact moved and nothing else: "
                 "gallery_recorded_size and result_pixel_size [768,448] -> [448,768], "
                 "result_is_portrait False -> True, result_quadrants ['red','green','blue',"
                 "'yellow'] -> ['blue','red','yellow','green'] == browser_truth, "
                 "result_matches_browser_truth False -> True, with gallery_workflow img2img and "
                 "stored_pixels_on_the_wire unchanged on both sides. The viewer shot shows it: a "
                 "wide landscape card BEFORE, an upright portrait one AFTER. Four traps cost a "
                 "run each. (1) /images/generate returns GalleryImage RECORDS, not data URLs -- "
                 "read width/height off the record and fetch the PNG from its authenticated "
                 "`url`. (2) An Apple MPS box cannot host this scene at all: z-image needs ~14 GB "
                 "and the load is refused outright, and the engine router only picks the "
                 "diffusers backend (the only one supporting img2img) on CUDA/ROCm/XPU -- so this "
                 "scene needs a real GPU, not just a fast machine. (3) On Modal, put the work "
                 "root and HF cache on the container's ephemeral disk, never a modal.Volume: uv "
                 "fails there with 'Could not persist temporary file ... Operation not "
                 "permitted'. (4) studio_test_kit.ui launches chromium with no args, which "
                 "refuses to start as root -- patch --no-sandbox into the CONTAINER copy.",
    ),
    10557: ScenePlan(
        pr=10557, scene="anthropic_context_window_truncation",
        what="the chat after an Anthropic reply fills the model's context window mid-word. "
             "The turn is driven for real through the photographed Studio's own backend; "
             "the connection's base URL points at a stand-in Messages API held by the "
             "scene that streams a sentence ending in 'decommis' and then reports "
             "stop_reason 'model_context_window_exceeded', so no key and no hosted call "
             "are needed while the request is still built by the real frontend and "
             "translated by the real _stream_anthropic",
        expect="BEFORE the unknown stop reason falls through to 'stop': the half-written "
               "answer is painted as a finished one, truncation_bar_present false and "
               "continue_button_present false. AFTER it maps to 'length' and carries the "
               "context_window_exceeded tool event: truncation_bar_present false -> true "
               "with truncation_bar_text reading \"Response filled the model's context "
               "window.\", and continue_button_present false -> true. assistant_text is "
               "the same cut-off sentence on both sides (assistant_ends_mid_word true on "
               "both) -- the reply is not what changed, what the UI says about it is.",
        verified="Exactly as expected, on base 580cffabac vs head 00f5e1a0f7. BEFORE: "
                 "truncation_bar_present false, continue_button_present false, outcome "
                 "'cut NOT reported: the half answer is painted as a finished one'. AFTER: "
                 "truncation_bar_text 'Response filled the model's context window. "
                 "Continue', both booleans true. The composite shows the same sentence "
                 "ending in 'decommis' on both sides with the bar only on the right. "
                 "One DUD fact: assistant_ends_mid_word reads true -> false, because "
                 ".aui-assistant-message-content also contains the bar's own text on the "
                 "AFTER side -- the reply itself is byte-identical up to 'decommis'. Match "
                 "the bar, not the tail of the message body. Pre-flight that saved a run: "
                 "drive the scene's own _sse() through the real _stream_anthropic on both "
                 "installs first (main gives finish_reason 'stop' and no tool event, head "
                 "gives 'length' plus the event); and the REGISTRY entry was silently lost "
                 "once between registering and running, so assert plan_for(PR) resolves "
                 "immediately before launching the driver.",
    ),
    10457: ScenePlan(
        pr=10457, scene="mlx_community_picker_rows",
        what="the chat model picker's On Device rows for two seeded cache repos, "
             "mlx-community/Qwen3-8B-4bit and unsloth/Qwen3-8B",
        expect="With the On Device list filtered to MLX: BEFORE the seeded "
               "mlx-community/Qwen3-8B-4bit is absent, because the picker called a repo MLX "
               "only when its name ended in -MLX; AFTER it is listed "
               "(mlx_filter_lists_subject False -> True, mlx_filter_row_count rises). The "
               "unsloth/Qwen3-8B control must be absent under the MLX filter on BOTH sides "
               "(mlx_filter_lists_control False unchanged), and both rows are listed under "
               "the All filter on both sides",
        verified="confirmed on base 502c4d237 (today's main; recommended-fit.ts is byte-identical "
                 "to the PR's own merge base 613309245) vs head ba11b5e0e. Facts moved as "
                 "predicted: mlx_filter_lists_subject false -> true, while mlx_filter_lists_control "
                 "stayed false and all_filter_lists_subject/control stayed true on BOTH sides. "
                 "The composite shows it: BEFORE the MLX filter reads 'No downloaded MLX models "
                 "yet.', AFTER it lists mlx-community/Qwen3-8B-4bit (8B, 524 KB, seeded) and "
                 "mlx-community/Qwen3-0.6B-4bit (0.6B, 335 MB, already in the cache). "
                 "mlx_filter_row_count is a DUD fact -- the rows are not role=option here, so it "
                 "reads 0 on both sides; use mlx_filter_text, which is the authoritative listing. "
                 "Three traps cost a rerun each: the picker opens on Recommended (must click On "
                 "Device), the inventory arrives async (a fixed sleep shot one side mid-'Loading "
                 "models...' and faked a delta -- poll until settled), and a substring row match "
                 "reports the unsloth control as listed because 'Qwen3-8B' is inside "
                 "'Qwen3-8B-4bit' -- match the leaf by exact line.",
    ),
    10458: ScenePlan(
        pr=10458, scene="local_thinking_level_turn",
        what="the composer's Think dropdown and the assistant bubble under it, on a resident "
             "Qwen3-0.6B GGUF loaded with a chat_template_override that is a real wide-ladder "
             "reasoning_effort template: it branches on 'none'|'minimal'|'low'|'medium'|'high'|"
             "'xhigh'|'max', so Studio's own detection publishes all seven and the Think menu "
             "offers exactly those on both sides. The template renders the level it was given "
             "into the system line the model echoes, so the reply IS the level llama-server was "
             "sent. 'Max' is picked through the real dropdown and the turn is sent from the real "
             "composer",
        expect="The Think menu must list the same seven rows on BOTH sides (None, Minimal, Low, "
               "Medium, High, Extra High, Max) and the trigger must read 'Thinking . Max' on "
               "both -- the menu is not what this PR changes, and a move there means the sides "
               "are not comparable. What moves is the reply: BEFORE (merge base 6133092453) the "
               "builder forwards only none/low/medium/high, so 'max' is dropped, no "
               "reasoning_effort reaches the template, and the bubble reads EFFORT=NOTHING "
               "(ui_effort_tag 'NOTHING', api_effort_tag 'NOTHING'). AFTER (head f50314305) the "
               "advertised level is forwarded and the bubble reads EFFORT=MAX (ui_effort_tag "
               "'MAX', api_effort_tag 'MAX').",
        kwargs={
            "model_path": "/Users/nilay/.cache/huggingface/hub/models--unsloth--Qwen3-0.6B-GGUF/"
                          "snapshots/50968a4468ef4233ed78cd7c3de230dd1d61a56b/"
                          "Qwen3-0.6B-Q4_K_M.gguf",
            "context_length": 2048,
            "level": "max",
        },
        needs_model=True,
        verified="Run 2026-09-07. Controls held: both sides list the same seven rows (None, "
                 "Minimal, Low, Medium, High, Extra High, Max), both triggers read "
                 "'Thinking \u00b7 Max', same active_model unsloth/Qwen3-0.6B-GGUF, same "
                 "reasoning_style 'reasoning_effort', api_http_status 200 on both. The reply "
                 "moved: BEFORE 'EFFORT=HIGH', AFTER 'EFFORT=MAX' (ui_effort_tag and "
                 "api_effort_tag both HIGH -> MAX). NOT the predicted 'NOTHING': picking a "
                 "level in the composer also turns thinking on, so the merge base falls to its "
                 "'high' if enable_thinking fallback instead of dropping the kwarg. The bare "
                 "kwarg drop is the raw-API path, where the Actions probe reads null. Either "
                 "way the top of the ladder was unreachable before the fix.",
    ),
    10454: ScenePlan(
        pr=10454, scene="toolless_switch_tool_history", needs_model=True,
        what="the chat thread after a Studio built-in tool (search_conversation) has "
             "already run in it and the chat has been switched to a GGUF whose template "
             "does not advertise tools (gemma-3-270m-it, 254 MiB). The thread is seeded "
             "through the real chat-history API so both sides replay identical stored "
             "bytes, the follow-up is sent through the real composer, and the same "
             "replayed conversation is also put to /v1/chat/completions and "
             "/v1/chat/count_tokens on the very server that was photographed",
        expect="BEFORE (merge base 6133092453) refuses every later turn of the thread: "
               "turn_http_status 400 with 'the current model/template does not advertise "
               "tools', turn_answer empty, and the context bar's recount 503s "
               "(count_http_status 503, count_input_tokens null), so the chat shows the "
               "failure and no assistant reply. AFTER (head 4ac462ef8) folds Studio's own "
               "tool turns into user text: turn_http_status 200 with a real "
               "turn_answer, count_http_status 200 with a real count_input_tokens, and "
               "the thread carries the assistant answer "
               "(ui_generation_failed_toast true -> false, ui_assistant_answered "
               "false -> true), with ui_follow_up_sent true on BOTH sides so the "
               "difference cannot be a missed composer.",
        kwargs={
            "model_path": "/Users/nilay/.cache/huggingface/hub/models--unsloth--gemma-3-270m-it-GGUF/"
                          "snapshots/c90975dbd40c0c7b275fefaae758c3415c906238/"
                          "gemma-3-270m-it-UD-Q4_K_XL.gguf",
            "context_length": 2048,
        },
        verified="Ran 2026-09-08 against base 6133092453 and head 4ac462ef8. As expected "
                 "on every measured key: turn_http_status 400 -> 200, turn_answer '' -> "
                 "'The sky is blue.', count_http_status 503 -> 200, count_input_tokens "
                 "null -> 115, ui_shows_tool_rejection true -> false, "
                 "ui_assistant_answered false -> true, ui_follow_up_sent true on BOTH. "
                 "The composite shows the red '400 ... does not advertise tools' banner "
                 "with Retry on the left and the answered turn at 208.7 tok/s on the "
                 "right; the context bar reads '- / 2.0k' before and '121 / 2.0k' after, "
                 "and the 'Used tool: search_conversation' card is identical on both "
                 "sides. ui_generation_failed_toast stayed false on both -- the failure "
                 "lands as a persistent in-thread banner, not the self-dismissing toast "
                 "the expectation named. Two earlier runs were discarded rather than "
                 "used: the first shot both sides before the thread repainted "
                 "(ui_follow_up_sent false on both), and the second reused the seeded "
                 "thread across runs, so a stale answer was on the page before the turn "
                 "was sent. The scene now uses a unique thread id AND title per run; "
                 "deleting the old thread is not an option, because Studio tombstones "
                 "the id and answers 410 to the re-create.",
    ),
    10455: ScenePlan(
        pr=10455, scene="anthropic_uncaptioned_image",
        what="the chat after attaching one image to the composer, typing nothing, and "
             "pressing send on an Anthropic connection. The turn is driven for real "
             "through the photographed Studio's own backend; the connection's base URL "
             "points at a stand-in Messages API held by the scene that enforces the one "
             "documented rule under test (an empty text block is a 400, 'text content "
             "blocks must be non-empty') and otherwise streams a normal answer, so no "
             "key and no hosted call are needed while the request is still built by the "
             "real frontend and translated by the real _stream_anthropic",
        expect="BEFORE (merge base 6133092453) puts an empty text block in front of the "
               "picture: wire_block_types ['text','image'], empty_text_block_sent true, "
               "the stand-in answers 400 with 'messages.0.content.0.text: text content "
               "blocks must be non-empty', and the chat shows the 'Generation failed' "
               "toast with no assistant answer (assistant_answered false). AFTER (head "
               "c52f7f98d) sends the image alone: wire_block_types ['image'], "
               "empty_text_block_sent false, provider_statuses [200], and the bubble "
               "reads the stand-in's answer (assistant_answered false -> true).",
        verified="Exactly as expected, on base 6133092453 vs head 705d270369, reproduced "
                 "on two independent runs. BEFORE: wire_block_types ['text','image'], "
                 "empty_text_block_sent true, provider_statuses [400], provider_error "
                 "'messages.0.content.0.text: text content blocks must be non-empty', "
                 "toast 'Generation failed ...', assistant_answered false. AFTER: "
                 "wire_block_types ['image'], empty_text_block_sent false, "
                 "provider_statuses [200], assistant_text 'A solid red square, and "
                 "nothing else in the frame.', assistant_answered true. Both sides typed "
                 "nothing (composer_text_typed '') and sent one request. The connection's "
                 "key must be seeded into unsloth_chat_external_provider_keys or the "
                 "adapter refuses the turn client-side and both sides photograph the same "
                 "'Missing API key for selected connection.' toast.",
    ),
    10317: ScenePlan(
        pr=10317, scene="gguf_hub_export_panel", needs_model=True,
        what="the Export panel after a GGUF export of a plain-directory Qwen2.5-0.5B-Instruct "
             "with destination 'Push to Hub', on a Mac where the exporter is unsloth_zoo's MLX "
             "path: the live log area (the worker's stdout), the elapsed timer, and the final "
             "banner. Both Studios run with HF_ENDPOINT pointed at a local stand-in Hub that "
             "answers whoami, repo creation and commits and logs what it received; the model, "
             "the llama.cpp conversion and the Studio are real on both sides",
        expect="BEFORE (merge base e8a88546d) runs the conversion twice: conversion_passes 2, "
               "quantize_passes 2, two 'GGUF export complete ->' lines whose second directory "
               "is named after the repo id (MLX push_to_hub_gguf passes repo_id as the save "
               "directory), and a longer elapsed. AFTER (head) runs it once: conversion_passes "
               "1, one complete dir, hub_files_committed includes the .gguf plus README.md from "
               "the model card. On BOTH sides the local .gguf lands and hub_push_started is "
               "True; if the stand-in Hub rejects a commit the run ends in an error AFTER the "
               "differing conversion counts, which still shows the change but must be said.",
        kwargs={
            "model_dir": "/Users/nilay/.claude/skills/pr-ui-evidence/temp/ev10317/models/Qwen2.5-0.5B-Instruct",
            "fake_hub_log": "/Users/nilay/.claude/skills/pr-ui-evidence/temp/ev10317/fakehub/requests.jsonl",
            "hf_username": "evidence-user",
            "model_name": "Qwen2.5-0.5B-Instruct-GGUF",
            "quant_label": "Q4_K_M",
        },
        verified="CONFIRMED by eye on two isolated installs, base e8a88546d vs head e6321ec93, each "
                 "stamped with its own SHA, identical unsloth_zoo 2026.9.1 (mlx/utils.py sha "
                 "dc5c451bb3fe) on both. Both panels: 'Export finished and pushed to Hugging Face "
                 "Hub.', Complete 100%. BEFORE: conversion_passes 2, quantize lines 4 (the MLX path "
                 "prints 'Quantizing to' twice per pass, so the predicted 2 was the wrong count, not "
                 "the wrong behaviour), export_complete_dirs [<export>/_tmp_model_*/model, "
                 "'evidence-user/Qwen2.5-0.5B-Instruct-GGUF'], elapsed 19s, 1818 log lines, Hub "
                 "received [Qwen2.5-0.5B-Instruct.Q4_K_M.gguf]. AFTER: conversion_passes 1, quantize "
                 "lines 2, one complete dir, elapsed 12s, 913 log lines, Hub received [the .gguf, "
                 "README.md]. pair_01, scrolled to the 'Pushing GGUF model to Hub' line, shows "
                 "BEFORE re-running 'Merging LoRA weights' / 'Converting to GGUF format' under it "
                 "and AFTER ending at that line. Stand-in Hub needed /api/validate-yaml (the card's "
                 "front-matter check) or AFTER errors after its GGUF commit.",
    ),
    10319: ScenePlan(
        pr=10319, scene="web_fetch_address_fallback",
        what="the two web_search cards and the assistant bubble after the model reads a "
             "dual-stack page (https://en.wikipedia.org/wiki/IPv6, whose AAAA sorts first on "
             "this host) and then an IPv4-only control (https://httpbin.org/html), while the "
             "launched Studios run with global IPv6 connect() failing ENETUNREACH -- the "
             "configured-but-dead IPv6 route of a broken VPN, arranged with --studio-env "
             "PYTHONPATH=<dir holding sitecustomize.py> so only the backend process sees it. "
             "Resolution is untouched; the fetches are real network calls on both sides",
        expect="BEFORE (merge base e8a88546d) pins the first resolved address only, so the "
               "first card's result pane reads 'Failed to fetch URL: <urlopen error [Errno 51] "
               "Network is unreachable>' (dual_stack_url_failed True, dual_stack_has_page "
               "False) while the control card carries the Moby-Dick excerpt. AFTER (head) "
               "walks to the IPv4 address: the first card carries the IPv6 article "
               "('From Wikipedia'), dual_stack_url_failed True -> False, "
               "dual_stack_result_len jumps into the thousands, page_shows_fetch_failure "
               "True -> False. The control MUST load on BOTH sides and "
               "backend_ipv6_dead_marker MUST be True on BOTH sides -- otherwise the pair "
               "is a network or harness difference and proves nothing about the change.",
        kwargs={},
        verified="CONFIRMED by eye on two isolated installs, base e8a88546d vs head 72555e2f7, "
                 "each stamped with its own SHA, both launched with "
                 "--studio-env PYTHONPATH=<v6dead dir>. backend_ipv6_dead_marker True on BOTH "
                 "sides (the marker pid is the pid listening on the Studio port). Both sides "
                 "issued the same two URLs, provider_completions=3, approvals_clicked=2, "
                 "tool_group_label '2 tool calls'. BEFORE: first card 'Failed to fetch URL: "
                 "<urlopen error [Errno 51] Network is unreachable>', dual_stack_result_len 165, "
                 "page_shows_fetch_failure True. AFTER: first card '# IPv6 From Wikipedia, the "
                 "free encyclopedia ...', dual_stack_url_failed True -> False, "
                 "dual_stack_result_len 165 -> 16037, page_shows_fetch_failure True -> False. "
                 "The control held: control_result_len 3598 on BOTH sides with the Moby-Dick "
                 "excerpt, so the two runs reached the same httpbin and only the pinned-address "
                 "walk decided whether wikipedia arrived. Only the five predicted keys moved.",
    ),
    10318: ScenePlan(
        pr=10318, scene="rag_whole_doc_project_merge",
        what="the Document Sources row under a real answer in a project chat that has a "
             "file attached to the thread, on a resident Qwen3-0.6B GGUF at a 2,560-token "
             "window. Whole-document mode runs only on the local inference paths, so a "
             "hosted turn would photograph nothing; one badge is rendered per distinct "
             "document that reached the model, which is the injected source list itself",
        expect="The attached meeting-notes.txt must be cited on BOTH sides -- losing it is "
               "a finding, not a smaller number. The four CJK project documents are what "
               "moves: BEFORE (merge base) admits the merged block on len(text)//4, the "
               "English rule, so project_documents_cited is 4 and source_badge_count 5. "
               "AFTER prices the same block with the serving model's own tokenizer, where "
               "a CJK character is about one token, so the row is trimmed to what fits "
               "beside the attachment -- project_documents_cited drops (expected 1-2) and "
               "source_badge_count with it, while attached_document_cited stays True.",
        kwargs={
            # Small, tool-capable, and fast to make resident: the scene needs a real
            # local turn, not a good answer. The window is deliberately tight -- the
            # budget is derived from it, and a large window leaves the CJK block fitting
            # on both measures, which photographs as no change.
            "model": "unsloth/Qwen3-0.6B-GGUF",
            "variant": "Q4_K_M",
            "context_length": 2560,
        },
        needs_model=True,
    ),
    # Local branch fix-detect-hermes-downloaded-models (no upstream PR yet); refs are
    # passed explicitly: --base-ref upstream/main 8e26d2c2b, --head-ref the branch head.
    900030: ScenePlan(
        pr=900030, scene="hermes_picker_rows",
        what="the chat model picker's On device tab, with a GGUF seeded exactly where "
             "Hermes Desktop's one-click download puts it (<HERMES_HOME>/models/"
             "Qwen3.8-27B-UD-Q4_K_M.gguf, projector under models/assets/), HERMES_HOME "
             "pointed at an isolated root on BOTH sides via --studio-env",
        expect="BEFORE (merge base) the picker lists no Qwen3.8-27B row and /api/hub/local "
               "reports no source=='hermes' rows (hermes_row_count 0, picker_shows_model "
               "false, hermes_dirs_scanned null). AFTER (head) the row is listed under "
               "Custom Folders and the inventory reports it as source 'hermes' "
               "(hermes_row_count 0 -> 1, picker_shows_model false -> true, "
               "hermes_dirs_scanned null -> [<root>/models])",
        kwargs={"hermes_root": "/Users/nilay/.claude/skills/pr-ui-evidence/temp/ev_hermes/hermes"},
        verified="2026-09-04, base 8e26d2c2b vs head e9230c2ee, macOS/Chromium 1440x1000: "
                 "BEFORE hermes_row_count 0, picker_shows_model false, hermes_dirs_scanned null; "
                 "AFTER 1 / true / [<root>/models], row 'Qwen3.8-27B-UD-Q4_K_M 27B' under "
                 "CUSTOM FOLDERS on the On Device tab. Two earlier runs failed for harness "
                 "reasons, not the PR: the kit's form-scoped model-picker-trigger testid no "
                 "longer exists (the trigger is the header's 'Select model' button), and "
                 "`tsc --noEmit -p tsconfig.json` checks nothing (solution tsconfig) so the "
                 "install's `tsc -b` gate caught two source unions the branch had missed.",
    ),

    10327: ScenePlan(
        pr=10327, scene="hermes_picker_rows",
        what="the chat model picker's On device tab, with Hermes Desktop's real download layout "
             "seeded under an isolated HERMES_HOME on BOTH sides via --studio-env: a single-file "
             "Qwen3.8-27B-UD-Q4_K_M.gguf, its projector under models/assets/, and the catalog's "
             "four-part Qwen3.8-Flash-Next-UD-Q4_K_XL split with parts of 1000/2000/3000/4000 "
             "bytes of padding. Facts come from the same server: /api/hub/local (the picker's "
             "inventory) and /api/models/local (the recipe picker, chat auto-load and /v1/models)",
        expect="BEFORE (merge base 55418be5e) lists nothing from Hermes: hermes_row_count 0, "
               "compat_hermes_row_count 0, hermes_dirs_scanned null, split_row_size_bytes null, "
               "picker_shows_model false, picker_shows_split false. AFTER (head) both inventories "
               "carry the two downloads: hermes_row_count 0 -> 2, compat_hermes_row_count 0 -> 2, "
               "hermes_dirs_scanned [<root>/models], the split listed once by part one with "
               "split_row_size_bytes == split_parts_on_disk_bytes (the whole set, not part one), "
               "and the composite shows both rows under Custom Folders on the On device tab only "
               "on the AFTER side.",
        kwargs={"hermes_root": "/Users/nilay/.claude/skills/pr-ui-evidence/temp/ev10327/hermes"},
        verified="2026-09-05, base 55418be5e vs head 4c1e25e57 (also 2c396214a), macOS/Chromium 1440x1000, both "
                 "Studios with HERMES_HOME plus isolated HF_HOME/HF_HUB_CACHE/HF_XET_CACHE/"
                 "XDG_CACHE_HOME (the legacy/default ~/.cache/huggingface roots are scanned "
                 "regardless, so the box's ambient rows appear on BOTH sides and are the "
                 "control). BEFORE: hermes_row_count 0, compat_hermes_row_count 0, "
                 "hermes_dirs_scanned null, split_row_size_bytes null, picker_shows_model/"
                 "split false, CUSTOM FOLDERS section empty. AFTER: 2 / 2 / [<root>/models] / "
                 "10668 == split_parts_on_disk_bytes 10668, split_row_path_is_part_one true, "
                 "and the composite shows 'Qwen3.8-Flash-Next-UD-Q4_K_XL' and "
                 "'Qwen3.8-27B-UD-Q4_K_M 27B' under CUSTOM FOLDERS only on AFTER. Two harness "
                 "lessons: a 6 s fixed wait photographed 'Loading models...' on AFTER (the scene "
                 "now waits for that text to clear), and BEFORE must scroll to the Custom "
                 "Folders heading or it photographs the top of the ambient list.",
    ),

    10254: ScenePlan(
        pr=10254, scene="research_report_budget",
        what="the Deep Research assistant message after a run on a SAVED CONNECTION whose "
             "Max Output Tokens is 32768, against a stand-in OpenAI-compatible server that "
             "honours the max_tokens it is sent -- truncating with finish_reason 'length' "
             "below the report's cost and running to a natural 'stop' at or above it. Each "
             "side sends what its own client could: the merge-base route rejects the whole "
             "request for an unknown maxOutputTokens, so that side falls back to the legacy "
             "payload exactly as the old client did",
        expect="BEFORE (merge base b319ba383) the client cannot send a ceiling and the report "
               "hop spends the hardcoded 16384, which cannot pay for the report, so the run "
               "is delivered truncated mid ```python fence under an 'Incomplete report.' "
               "callout. AFTER (head) the run carries the connection's resolved 32768 and the "
               "report completes, with no callout at all "
               "(client_sent_ceiling false -> true, synthesis_max_tokens 16384 -> 32768, "
               "has_incomplete_notice true -> false, report_ran_to_completion false -> true, "
               "report_truncated_mid_fence true -> false; run_status completed on BOTH sides)",
    ),

    10221: ScenePlan(
        pr=10221, scene="web_fetch_unusable_charset",
        what="the web_search tool cards and the assistant bubble in the chat UI after the "
             "model reads four real public URLs in one turn, each one httpbin's "
             "/response-headers echoing a different declared charset -- unicode, base64, "
             "idna, and utf-8 as the control. The page has to be public because "
             "_validate_and_resolve_host refuses any non-global address, so this is a real "
             "network fetch on both sides and the only variable is what the backend makes "
             "of the label",
        expect="BEFORE (merge base c9f98dbd5) the first three cards show the result pane "
               "reading 'Failed to fetch URL: unknown encoding: unicode', \"'base64' is not "
               "a text encoding\" and 'Unsupported error handling: replace', the assistant "
               "bubble says 1 of 4 returned the page, and page_shows_fetch_failure is true. "
               "AFTER (head) all four cards show the echoed page body containing MARKERWORD, "
               "the bubble says 4 of 4, and page_shows_fetch_failure is false. The utf-8 "
               "control card MUST read the page on BOTH sides -- if utf-8 fails on either, "
               "the run is a network or harness problem and proves nothing about the change.",
        kwargs={},
        verified="confirmed by eye at ba5f63a73 vs c9f98dbd5 on two isolated installs. Both "
                 "sides issued the same four URLs, rendered '4 tool calls', and took 4 Allow "
                 "clicks, so they are comparable. BEFORE pages_read=1: the cards read 'Failed "
                 "to fetch URL: unknown encoding: unicode', \"'base64' is not a text encoding; "
                 "use codecs.decode() to handle arbitrary codecs\" and 'Unsupported error "
                 "handling: replace', and the bubble says '1 returned the page (utf-8) and 3 "
                 "failed to fetch (unicode, base64, idna)'. AFTER pages_read=4: every card "
                 "shows the echoed body with MARKERWORD and the bubble says '4 returned the "
                 "page (unicode, base64, idna, utf-8)'. page_shows_fetch_failure true -> false. "
                 "The utf-8 control read the page on BOTH sides, identical 120-char result, "
                 "which is what makes the other three comparable. Note the BEFORE tool result "
                 "also appended 'The tool call encountered an issue. Please try a different "
                 "approach or rephrase your request.', so the model was actively steered off "
                 "the page rather than merely handed less text.",
    ),
    10224: ScenePlan(
        pr=10224, scene="apple_free_memory_row",
        what="the GPU device row in Settings -> System on a real Apple Silicon Mac "
             "(M5 Pro, 24 GB unified, Metal working set 17.76 GB), photographed next to "
             "what the two backend endpoints say free VRAM is: /api/system, which the row "
             "renders, and /api/system/hardware, which the training-method picker reads",
        expect="BEFORE (merge base c9f98dbd5) the row reads '0.85 GiB used | 23.1 GiB free | "
               "24 GiB total' -- system RAM minus GPU-only usage, so every other process on "
               "the machine counts as free VRAM, and the RAM tile directly above it on the "
               "same screen says only 7.38 GiB is free. AFTER (head) the same row reports "
               "what a new allocation can actually get, bounded by the Metal working set, so "
               "tab_vram_free_gb drops from 23.15 to roughly the host's available RAM and "
               "lands near the RAM tile's own figure. training_method_by_model_size_gb flips "
               "the larger sizes from lora to qlora. NOTE: endpoints_agree is true on BOTH "
               "sides -- on the merge base both endpoints are wrong together, and after the "
               "fix both are right together. The disagreement (tab 23.62 vs picker 11.43) "
               "existed only at the PR's first commit, which corrected the picker's endpoint "
               "and not the tab's; commit 0eeb685 closed it. Absolute GiB figures drift with "
               "host memory between the two installs.",
        verified="confirmed by eye at head 2c26ac953 vs c9f98dbd5 on two isolated installs "
                 "of a real M5 Pro (24 GB unified): the GPU row goes '0.88 GiB used | 23.1 "
                 "GiB free | 24 GiB total' -> '0.34 GiB used | 6.67 GiB free | 24 GiB "
                 "total', and the VRAM tile above it 23.1 -> 6.67 GiB free. The two sides "
                 "are comparable: the RAM tile reads 6.39 GiB free BEFORE and 6.34 GiB "
                 "free AFTER, so BEFORE claims 23.1 GiB of VRAM free on a machine with "
                 "6.39 GiB of RAM free, and AFTER agrees with the RAM tile. "
                 "training_method_by_model_size_gb flips 5/6/7/8/10/13 GB models from lora "
                 "to qlora. endpoints_agree is true on both sides, as predicted. Two "
                 "earlier attempts died before the shot: open_chat's 30s /chat navigation, "
                 "then an exact-text role lookup for the System tab -- the tab carries an "
                 "icon in its accessible name, so the scene uses the "
                 "data-testid=settings-tab-resources handle the dialog provides.",
    ),
    10219: ScenePlan(
        pr=10219, scene="final_answer_continuation",
        what="a seeded two-exchange thread in the chat UI, with unsloth/gemma-3-270m-it-GGUF "
             "Q4_K_M resident at a 1024-token window and one decode slot, asked for a 3000-word "
             "story so the answer genuinely runs out of room mid-sentence and Studio sends the "
             "partial back with continue_final_message",
        expect="BEFORE (merge base c9f98dbd5) the continuation carries continue_final_message "
               "with no add_generation_prompt, llama-server defaults that to true and refuses "
               "the pair, so the chat keeps roughly 1.3k characters of story ending mid-sentence "
               "and shows the 'Cannot set both add_generation_prompt and continue_final_message "
               "to true' error: ui_shows_flag_error true. AFTER (head) both permitted "
               "continuations are served, answer_chars rises to roughly 4.4k, the story reads on "
               "past where it was cut, and ui_shows_flag_error is false. ui_story_prompt_sent "
               "must be true on BOTH sides; if it is not, the composer was missed and the pair "
               "proves nothing.",
        kwargs={
            "model_path": "/Users/nilay/.cache/huggingface/hub/models--unsloth--Qwen3-0.6B-GGUF/"
                          "snapshots/50968a4468ef4233ed78cd7c3de230dd1d61a56b/"
                          "Qwen3-0.6B-Q4_K_M.gguf",
            "context_length": 1024,
        },
        needs_model=True,
    ),
    10220: ScenePlan(
        pr=10220, scene="research_truncated_report",
        what="the Deep Research assistant message after a run on a SAVED CONNECTION whose "
             "synthesis stopped at the provider's own output cap -- the same stand-in "
             "OpenAI-compatible server as PR 10166, but reporting fewer completion tokens "
             "than synthesis asked for, which is what a provider-side cap looks like",
        expect="The 'Incomplete report.' callout above the report changes wording. BEFORE "
               "(merge base) a cloud run is blamed on the local model: 'Local model report "
               "hit the loaded context window before completion. Increase Context Length in "
               "chat settings or reduce the research evidence size.' AFTER (head) it names "
               "the connection that actually ran: 'Connected model report reached its "
               "output limit before completion.' "
               "(notice_blames_local_context true -> false, notice_names_a_local_model "
               "true -> false, truncation_notice text differs; the report body, "
               "run_status and model_phases are IDENTICAL on both sides)",
        kwargs={"completion_tokens": 8192},
        verified="confirmed on base c9f98dbd5 vs head 0a8ac8eca, two isolated installs. "
                 "Every predicted fact moved and nothing structural did: "
                 "notice_blames_local_context true -> false, notice_names_a_local_model "
                 "true -> false, truncation_notice 'Local model report hit the loaded "
                 "context window before completion. Increase Context Length in chat "
                 "settings or reduce the research evidence size.' -> 'Connected model "
                 "report reached its output limit before completion.' model_phases is "
                 "IDENTICAL on both (planning, decision, synthesis_audit, synthesis, "
                 "synthesis_recovery), run_status completed on both, and synthesis "
                 "max_tokens is 16384 on both -- this scene loads no local model, so it "
                 "isolates the WORDING half of the PR; the sizing half needs a loaded "
                 "small-context GGUF and is proved separately. report_chars 825 -> 745 "
                 "only because the new notice is shorter. NOTE: the three source pills "
                 "under the report differ between the sides -- the single research step's "
                 "search results are not pinned -- which is incidental and outside the "
                 "claim.",
    ),

    10222: ScenePlan(
        pr=10222, scene="ollama_pull_on_device_row",
        what="the Hub's On Device list filtered to \"qwen\", against an OLLAMA_MODELS store "
             "holding one real `ollama pull qwen2.5:0.5b` (manifest: image.model "
             "397,807,936 B + image.system 68 B + image.template 1,482 B + image.license "
             "11,343 B), with the same server's /api/hub/local counted by source",
        expect="BEFORE (merge base) the manifest's image.template and image.system layers "
               "are not in the loadable set, so the scan withholds the row: rows_ollama 0, "
               "ollama_display_names [], nothing matching qwen on the page, and the list "
               "renders its empty state even though the weights are on disk. AFTER (head) "
               "the row is listed: rows_ollama 0 -> 1, display name "
               "'qwen2.5:0.5b (494.03M Q4_K_M)', its load_id is an ollama-manifest: ref, "
               "and the qwen row is visible in the photographed list",
        kwargs={"search_term": "qwen"},
        verified="confirmed by eye at head 703b9f829 vs c9f98dbd5 on two isolated installs: "
                 "rows_ollama 0 -> 1, ollama_display_names [] -> "
                 "['qwen2.5:0.5b (494.03M Q4_K_M)'], rows_total 64 -> 65. BEFORE the list "
                 "holds only Qwen3 safetensors rows out of the HF cache; AFTER it carries "
                 "the Ollama row (subtitle 'Ollama · GGUF · 0.5B') and its detail pane "
                 "offers Run. First run photographed 'Loading local inventory...' on BOTH "
                 "sides on a 12s fixed settle -- the scene now waits on that spinner "
                 "clearing instead, which is what makes the pair mean anything.",
    ),
    10216: ScenePlan(
        pr=10216, scene="run_settings_save_without_load",
        what="the chat picker's run-settings panel for a downloaded, UNLOADED GGUF "
             "(unsloth/gemma-3-270m-it-GGUF UD-Q4_K_XL seeded into an isolated HF cache): "
             "advanced settings opened, Parallel decode slots set to 2, Remember ticked, "
             "then the footer buttons read and the server's API-load overrides and loaded "
             "list read back",
        expect="BEFORE (merge base 2e6db0c12) the footer offers only Reset and Load model "
               "(save_buttons 0), so nothing can be saved without loading: override_after "
               "stays false and the model stays unloaded. AFTER (head 34bd84cb9) a Save "
               "settings button sits beside Load model (save_buttons 0 -> 1); clicking it "
               "toasts 'Settings saved.', the server override under override_key (the repo "
               "plus the quant the panel actually opened, which the picker's expander "
               "chooses by fit and need not be the seeded one) appears with n_parallel 2 "
               "(override_after false -> true), and loaded_after stays [] on BOTH sides -- "
               "the settings were saved without ever loading the model.",
        kwargs={"hf_home": "/Users/nilay/.claude/skills/pr-ui-evidence/outputs/ui_diff_10168/hf"},
        verified="confirmed by eye, 2e6db0c12 vs 34bd84cb9 on two isolated installs. "
                 "footer_buttons [Reset, Load model] -> [Reset, Save settings, Load model]; "
                 "toast 'Settings saved.'; override_row null -> {custom_context_length: 4096}; "
                 "loaded_after [] and active_model_after null on BOTH sides. The panel opened "
                 "Q4_K_M, not the seeded UD-Q4_K_XL -- the expander orders quants by fit, so "
                 "the scene follows opened_quant rather than the seeded one; an earlier run "
                 "keyed the override lookup to the seed and read a working save as 'nothing "
                 "saved'. Both homes are reused between runs, so the pre-clean clears every "
                 "override key, not just one: without that, a leftover row from the previous "
                 "run showed up as override_after false -> true that this run had not caused.",
    ),
    10168: ScenePlan(
        pr=10168, scene="run_settings_save_without_load",
        what="the chat picker's run-settings panel for a downloaded, UNLOADED GGUF "
             "(unsloth/gemma-3-270m-it-GGUF UD-Q4_K_XL seeded into an isolated HF cache): "
             "advanced settings opened, Parallel decode slots set to 2, Remember ticked, "
             "then the footer buttons read and the server's API-load overrides and loaded "
             "list read back",
        expect="BEFORE (upstream/main c9f98dbd5) the footer offers only Reset and Load model "
               "(save_buttons 0), nothing is saved (override_after false) and the model "
               "stays unloaded. AFTER (head a09d5bf2c) a Save settings button sits beside "
               "Load model (save_buttons 0 -> 1); clicking it toasts 'Settings saved.', the "
               "server override for unsloth/gemma-3-270m-it-GGUF:UD-Q4_K_XL appears with "
               "n_parallel 2 (override_after false -> true) and loaded_after stays [] on "
               "both sides, i.e. saved without loading",
        kwargs={"hf_home": "/Users/nilay/.claude/skills/pr-ui-evidence/outputs/ui_diff_10168/hf"},
        verified="confirmed by eye at head a09d5bf2c vs c9f98dbd5 on two isolated installs: "
                 "footer_buttons [Reset, Load model] -> [Reset, Save settings, Load model], "
                 "toast 'Settings saved.', override_row null -> {n_parallel: 2}, loaded_after [] "
                 "on both sides. The first head (beaaa7f8a) truncated the remember label to "
                 "'Remember for thi...' in the picker popover; a09d5bf2c wraps the footer.",
    ),
    10160: ScenePlan(
        pr=10160, scene="api_load_settings_forget",
        what="the \"Settings applied on API load\" panel on /api-monitor, with two "
             "overrides seeded through the photographed Studio's own overrides route: a "
             "repo the picker still lists, and the deleted custom folder from issue "
             "#10159 whose settings page can never be reached again. The scene tries to "
             "forget the folder row and reads the panel and the server back",
        expect="BEFORE (PR merge base) the rows are read-only: no forget control exists "
               "(forget_buttons 0), the folder entry survives and keeps applying to every "
               "API request that names it (rows_after_forget still 2, "
               "folder_entry_still_applies true), and the repo row reads "
               "'1 parallel slots \u00b7 1 checkpoints \u00b7 1 GPU layers \u00b7 1 MoE "
               "layers on CPU'. AFTER (head) each row carries a forget button "
               "(forget_buttons 0 -> 2), clicking it drops the row and the server key "
               "(rows_after_forget 2 -> 1, server_keys_after_forget 2 keys -> 1, "
               "folder_entry_still_applies true -> false), and the same repo row reads "
               "'1 parallel slot \u00b7 1 checkpoint \u00b7 1 GPU layer \u00b7 1 MoE "
               "layer on CPU'",
    ),
    10165: ScenePlan(
        pr=10165, scene="compaction_image_turn_instruction",
        what="the reply a resident local model gives after its chat is compacted, in a "
             "thread opened with a picture and a standing instruction typed beside it: "
             "the whole history posted straight at the photographed Studio's own "
             "/v1/chat/completions (greedy, capped), and the same thread in the real "
             "chat UI with the compaction notice above the reply",
        expect="BEFORE (PR merge base) the image turn is priced at the whole message, so "
               "the instruction typed beside the picture never enters the carried-forward "
               "block and the reply does not end with BLUEBERRY-7788 "
               "(probe_followed_standing_instruction false). AFTER (head) the turn is "
               "priced on the words that actually reach the block, the instruction is "
               "carried, and the reply ends with BLUEBERRY-7788 "
               "(probe_followed_standing_instruction false -> true, "
               "ui_followed_standing_instruction false -> true). "
               "probe_checkpoint_started must read true on BOTH sides; if it does not, "
               "no compaction happened and the pair proves nothing. The question asks "
               "about the PADDING on purpose: the archive recall also runs on a reset, "
               "and a question unrelated to everything lets the lone distinctive chunk "
               "-- the instruction itself -- win retrieval on both sides, masking what "
               "the block carried.",
        kwargs={
            "model_path": "/Users/nilay/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-GGUF/"
                          "snapshots/ecc8b33b2c50598815e4b0f7cea6088e3ae7adb8/"
                          "gemma-4-E2B-it-UD-Q4_K_XL.gguf",
            "context_length": 2048,
        },
        needs_model=True,
    ),
    10166: ScenePlan(
        pr=10166, scene="research_truncated_report",
        what="the Deep Research assistant message after a run whose synthesis stopped on "
             "`length` twice -- driven end to end through the real backend against a saved "
             "OpenAI-compatible connection served from the scene process, so no weights, "
             "GPU or download is involved",
        expect="BEFORE (merge base) the recovery draft replaces the first no matter what and "
               "a second `length` raises, so the run ends failed and NO report is shown; "
               "AFTER (head) the longer first draft is kept and delivered, led by an "
               "'Incomplete report.' callout that renders as a quote rather than as code "
               "(run_status failed -> completed, report_delivered false -> true, "
               "has_incomplete_notice false -> true, notice_leads_report false -> true)",
        verified="confirmed on base 5c8c238e6 vs head c5daca147, two isolated installs, "
                 "driven through the real ResearchSupervisor against a saved 'custom' "
                 "connection served from the scene process. Facts moved exactly as "
                 "predicted and nothing else did: run_status failed -> completed, "
                 "report_delivered false -> true, first_draft_survived false -> true, "
                 "has_incomplete_notice false -> true, notice_leads_report false -> true, "
                 "notice_inside_a_code_block false on BOTH sides. model_phases is IDENTICAL "
                 "on both -- planning, decision, synthesis_audit, synthesis, "
                 "synthesis_recovery, recovery_pass_ran true on each -- so the two Studios "
                 "did the same work and only the outcome differs, and BEFORE's report_sha is "
                 "the SHA-256 of the empty string. The composite reads plainly: BEFORE a "
                 "'Research could not be completed / Local model report reached its output "
                 "limit before completion' card and no report at all; AFTER 'Deep research "
                 "completed', the 'Incomplete report.' notice rendered as a blockquote ABOVE "
                 "the report, then Findings and a highlighted python block truncated "
                 "mid-line at 'rope_scaling ='. NOTE for whoever edits this scene: an "
                 "earlier pass had every fact moving while the composite showed nothing, "
                 "because the notice leads the report and a long draft scrolled it off the "
                 "top -- the draft is deliberately short and the shot is anchored on the "
                 "question so the callout is in frame. web_search is proxied to a dead port "
                 "on both sides (the model call sets trust_env=False), so the single "
                 "research step fails identically and no network or weights are involved.",
    ),

    10167: ScenePlan(
        pr=10167, scene="mcp_oauth_dual_client_auth",
        what="the MCP Servers dialog after adding an OAuth server whose authorization "
             "server rejects a token request that carries BOTH an HTTP Basic header and a "
             "client_id form field -- the rule Notion enforces, reproduced by a local "
             "authorization server so no account or human consent screen is involved",
        expect="BEFORE (merge base) the token exchange is rejected: the dialog shows the "
               "connection error containing 'Client must not use multiple authentication "
               "methods' and the server lists no tools; AFTER (head) the same flow "
               "authenticates and the dialog lists the server's tools "
               "(token_status 400 -> 200, tools_listed 0 -> 3, "
               "token_request_had_client_id true -> false)",
        kwargs={"tool_names": ["search", "fetch_page", "create_page"]},
        verified="confirmed on base 5c8c238e6 vs head cf72bdef2, two isolated installs on "
                 ":9707/:9709 with PR_UI_PORT_BASE=9700. Every predicted key moved: "
                 "token_status 400 -> 200, tools_listed 0 -> 3, token_request_had_client_id "
                 "true -> false, mcp_authenticated_requests 0 -> 6, and "
                 "token_request_form_keys drops exactly one field (client_id) and nothing "
                 "else. Both sides were assigned the same token_endpoint_auth_method "
                 "(client_secret_basic), so only the token request differs. NOTE for reruns: "
                 "the consent stand-in passed as BROWSER must return IMMEDIATELY and do its "
                 "fetch in the background, because webbrowser.open() uses GenericBrowser, "
                 "which blocks on p.wait(), while fastmcp starts its OAuth callback server "
                 "only after redirect_handler returns; a foreground curl deadlocks and the "
                 "flow dies on 'OAuth callback timed out after 300.0 seconds'. The helper "
                 "must also wait for the callback port to listen before following the "
                 "authorize URL. Default ports collide with concurrent sessions: set "
                 "PR_UI_PORT_BASE.",
    ),
    10141: ScenePlan(
        pr=10141, scene="mcp_oauth_dual_client_auth",
        what="the MCP Servers dialog after adding an OAuth server whose authorization "
             "server rejects a token request that carries BOTH an HTTP Basic header and a "
             "client_id form field -- the rule Notion enforces, reproduced by a local "
             "authorization server so no account or human consent screen is involved",
        expect="BEFORE (merge base) the token exchange is rejected: the dialog shows the "
               "connection error containing 'Client must not use multiple authentication "
               "methods' and the server lists no tools; AFTER (head) the same flow "
               "authenticates and the dialog lists the server's tools "
               "(token_status 400 -> 200, tools_listed 0 -> 3, "
               "token_request_had_client_id true -> false)",
        kwargs={"tool_names": ["search", "fetch_page", "create_page"]},
        verified="confirmed on base 1af3000fc vs head e982e98d9, two isolated installs on "
                 ":8990/:8991, driven through the real MCP Servers dialog. Every predicted "
                 "key moved and the flow is otherwise identical: both sides registered "
                 "dynamically and were assigned the SAME token_endpoint_auth_method "
                 "(client_secret_basic), so the authorization server treated them alike and "
                 "only the token request differs. token_request_form_keys drops exactly one "
                 "field, client_id, and nothing else; token_status 400 -> 200; tools_listed "
                 "0 -> 3; mcp_authenticated_requests 0 -> 6, so BEFORE never reached the MCP "
                 "endpoint at all. The composite reads plainly: the same dialog and the same "
                 "row on both sides, with the toast going from 'Refresh failed ... Token "
                 "exchange failed (400): invalid_request / Client must not use multiple "
                 "authentication methods' to 'Refreshed \"Notion MCP (local stand-in)\" "
                 "(3 tools)'. The authorization server is a local stand-in, not "
                 "mcp.notion.com, because the real flow needs a human at Notion's consent "
                 "screen; its DCR assignment and its /token rejection rule were both read "
                 "off the live Notion server first, and a live check against "
                 "mcp.notion.com/token confirms the fixed request shape gets past client "
                 "authentication (invalid_grant on a dummy code, not invalid_request).",
    ),
    10088: ScenePlan(
        pr=10088, scene="mcp_images_to_model",
        what="the chat answer a vision model gives after a stdio MCP tool returns a "
             "picture, driven through the real tool loop against a local "
             "OpenAI-compatible provider that answers with what its request actually "
             "carried",
        expect="BEFORE (PR merge base) the second completion carries only the tool's "
               "note, so the provider is handed no image part and the reply reads "
               "\"I cannot see the screenshot\"; AFTER (PR head) the backend promotes "
               "the returned picture into its own user turn and the reply reads "
               "\"I can see the screenshot: it is a solid blue square\" "
               "(post_tool_image_parts 0 -> 1, promoted_turn_text \"\" -> "
               "\"Images returned by the tool call above:\", tool note "
               "\"1 image attached; displayed to the user\" -> \"1 image returned\")",
        verified="confirmed on base 74661077e vs head b420cb84f, two isolated installs on "
                 ":8990/:8991. Every predicted key moved and nothing else did: "
                 "post_tool_image_parts 0 -> 1, post_tool_roles gained a trailing \"user\" "
                 "turn, promoted_turn_text \"\" -> \"Images returned by the tool call "
                 "above:\", tool_note_to_model \"[1 image attached; displayed to the "
                 "user]\" -> \"[1 image returned]\". Both sides agree "
                 "tool_offered_to_model=true and provider_completions=2, so the tool ran "
                 "identically and only what the second request carried differs; "
                 "envelope_leaked_to_provider=false on BOTH, so the base64 is never relayed "
                 "as tool text either way. The composite shows the symptom plainly: the same "
                 "blue square renders in the tool card on both sides, and the reply under it "
                 "goes from \"I cannot see the screenshot, so I can only guess what is in "
                 "it.\" to \"I can see the screenshot: it is a solid blue square.\" The "
                 "provider is a stand-in that answers from what its own payload held, so the "
                 "sentence is a readout of the request the backend built, not a scripted line. "
                 "Base was passed explicitly but 74661077e IS the PR's real merge base after "
                 "the branch was rebuilt onto fresh main. RE-RUN against 32dcc75c1 vs "
                 "423e534b5 after the #10092 rebase and twelve review rounds: identical "
                 "verdict on every key, so the twelve rounds of marker/provenance/cap work "
                 "left the user-visible behaviour where it started. BEFORE reply 11.84s, "
                 "AFTER 6.75s; transcript_char_count 554 -> 519.",
    ),

    10097: ScenePlan(
        pr=10097, scene="agents_tab_roster",
        what="the agent roster in Settings -> Agents of the photographed Studio: the "
             "backend's own /api/settings/coding-agents answer, then the opened roster "
             "dropdown that maps over it.",
        expect="BEFORE (merge base) the roster is the five listed agents -- Claude Code, "
               "OpenAI Codex, Hermes Agent, OpenClaw, OpenCode -- and api_agents holds six "
               "ids (the five plus the hidden 'pi'); api_lists_dsh false and "
               "ui_lists_deepseek_harness false. AFTER (head) 'dsh' joins CODING_AGENTS and "
               "a 'DeepSeek Harness' row joins the dropdown: api_agent_count 6 -> 7, "
               "roster_count 5 -> 6, api_lists_dsh and ui_lists_deepseek_harness both true. "
               "control_ui_lists_opencode must read true on BOTH sides; if it does not, the "
               "click missed or the panel never painted and the pair proves nothing.",
        kwargs={},
        verified="confirmed on merge base 2da377132 vs head ad4c1b2c9, two isolated installs "
                 "on ports 9320/9321, Chromium 1280x900. BEFORE roster: Claude Code, OpenAI "
                 "Codex, Hermes Agent, OpenClaw, OpenCode (roster_count 5, api_agent_count 6, "
                 "api_lists_dsh false, intro_mentions_dsh false). AFTER adds a 'DeepSeek "
                 "Harness' row carrying the blue 'ds' mark (roster_count 6, api_agent_count 7, "
                 "api_lists_dsh true) and the intro paragraph gains 'DeepSeek Harness'. "
                 "control_ui_lists_opencode true on both sides.",
    ),
    10094: ScenePlan(
        pr=10094, scene="webp_image_with_tools",
        what="two real /v1/chat/completions turns against the photographed Studio carrying the "
             "same WebP screenshot -- one with tools off, one with tools on -- followed by the "
             "API monitor page that recorded them.",
        expect="BEFORE (merge base) the tools-off turn succeeds and reads the word, while the "
               "tools-on turn sends the WebP bytes through untouched and llama-server refuses "
               "the image: tools_on_http_status non-200 (or tools_on_reads_word false). AFTER "
               "(head) the same tools-on turn is re-encoded to PNG and answers: "
               "tools_on_http_status 200 and tools_on_reads_word true. "
               "control_tools_off_http_status must read 200 and control_tools_off_reads_word "
               "true on BOTH sides; if not, the model, port or install differs and the pair "
               "proves nothing.",
        kwargs={
            "model_path": "/Users/nilay/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-GGUF/"
                          "snapshots/ecc8b33b2c50598815e4b0f7cea6088e3ae7adb8/"
                          "gemma-4-E2B-it-UD-Q4_K_XL.gguf",
            "context_length": 4096,
        },
        needs_model=True,
        verified="confirmed on merge base 2da377132 vs head 22779f4f5, two isolated installs "
                 "(:9250 / :9251), same gemma-4-E2B-it-UD-Q4_K_XL and the same llama.cpp "
                 "b10715-92cedc867 on both sides (the AFTER home's install had fallen back to a "
                 "source build, so the prebuilt was copied across before the run). Both Studios "
                 "ran with PATH=/usr/bin:/bin:/usr/sbin:/sbin, i.e. no ffmpeg -- the default "
                 "install, since Unsloth does not install ffmpeg and llama.cpp b10715 reads WebP "
                 "only by shelling out to it. control_tools_off stayed 200/reads-word/'stop' on "
                 "both sides, so the pair is comparable; tools_on moved 400 -> 200, reads_word "
                 "false -> true. The composite shows it: BEFORE a red /chat/completions row "
                 "carrying llama-server's own 'Failed to load image or audio file', AFTER the "
                 "same turn green and answering UNSLOTH. With ffmpeg on PATH both sides pass and "
                 "the pair is a null result -- that run is recorded too.",
    ),
    10096: ScenePlan(
        pr=10096, scene="nonstream_disconnect_monitor",
        what="the API monitor page of the photographed Studio after two real non-streaming "
             "POST /v1/messages turns against a live llama-server: a control turn that is "
             "allowed to finish, then a 400-token turn whose socket is reset 0.4 s in, so "
             "uvicorn reports a genuine client disconnect.",
        expect="BEFORE (merge base) the abandoned turn is not noticed: the model keeps "
               "decoding for the client that left, the run is held for seconds after the "
               "abort (held_after_client_left_s well above zero), and the monitor writes it "
               "down as a normal answer -- abandoned_row_status 'completed' with a full "
               "abandoned_completion_tokens and a green row. AFTER (head) the disconnect "
               "cancels the generation almost immediately (held_after_client_left_s drops to "
               "about one poll interval, abandoned_completion_tokens far below 400) and the "
               "row is recorded as cancelled: abandoned_row_status 'completed' -> "
               "'cancelled', abandoned_stop_reason -> None, and the page shows an amber "
               "cancelled row above the still-green control row. control_http_status must "
               "read 200 on BOTH sides; if it does not, the model, port or install differs "
               "and the pair proves nothing.",
        kwargs={
            "model_path": "/Users/nilay/.cache/huggingface/hub/models--unsloth--Qwen3-1.7B-GGUF/"
                          "snapshots/d7f544eead698dbd1f15126ef60b45a1e1933222/"
                          "Qwen3-1.7B-UD-Q4_K_XL.gguf",
            # 2048, not 4096: both Studios are alive at once on this Mac's shared
            # unified memory, and the second load refuses at 4096.
            "context_length": 2048,
        },
        needs_model=True,
    ),
    9923: ScenePlan(
        pr=9923, scene="grammar_bound_tool_turn",
        what="a real tool-bearing /v1/chat/completions turn against the photographed Studio, "
             "its catalog carrying a string maxLength of 2000 and an array maxItems of 1998 -- "
             "the first value each keyword's grammar cannot compile, measured against llama.cpp "
             "b10639 and b10679 -- followed by the API monitor page that recorded the turn. A "
             "control turn carrying the highest bound each keyword does compile (1999 / 1997) "
             "runs on both sides.",
        expect="BEFORE (merge base) the bounded turn is forwarded verbatim, llama-server refuses "
               "to compile the grammar, and the request dies: bounded_http_status 400 with "
               "bounded_grammar_error true, and the monitor page shows the failed turn. AFTER "
               "(head) the same catalog has both bounds stripped from the copy sent to "
               "llama-server and the turn completes: bounded_http_status 400 -> 200, "
               "bounded_grammar_error true -> false. control_http_status must read 200 on BOTH "
               "sides; if it does not, the model, port or install differs and the pair proves "
               "nothing.",
        kwargs={
            "model_path": "/Users/nilay/.cache/huggingface/hub/models--unsloth--Qwen3-1.7B-GGUF/"
                          "snapshots/d7f544eead698dbd1f15126ef60b45a1e1933222/"
                          "Qwen3-1.7B-UD-Q4_K_XL.gguf",
            "context_length": 4096,
        },
        needs_model=True,
        verified="confirmed on merge base 450299909 vs head 353739070, two isolated installs "
                 "(:8992 / :8993), same Qwen3-1.7B-UD-Q4_K_XL and llama.cpp b10672-760fb1c76 on "
                 "both sides. control_http_status stayed 200 either side, so the pair is "
                 "comparable; bounded_http_status moved 400 -> 200 and the old message's "
                 "model/quant blame disappeared (bounded_blames_model_or_quant true -> false). "
                 "The composite shows it: BEFORE the API monitor lists a red /chat/completions "
                 "row carrying {\"code\":400,...\"failed to parse grammar\"} above the green "
                 "control turn; AFTER the same two turns are both green (474 ms / 168 tok/s and "
                 "864 ms / 166 tok/s) with the prompt preview intact.",
    ),
    99053: ScenePlan(
        pr=99053, scene="glm53_tool_call_replay",
        what="a follow-up turn on a stored thread that already holds a render_html tool "
             "call, under a 0.5B GGUF carrying the real 252-line zai-org/GLM-5.3 chat "
             "template (md5 e4c9e1000a513f680dd796f76d1097e0), plus a "
             "/v1/chat/completions probe of the same replayed conversation against the "
             "server that was photographed. Local branch, not a GitHub PR: base "
             "5fdcf9cc7 is the branch point, head 6d17805ea the fix.",
        expect="BEFORE llama-server is launched with the GGUF's own template, whose line "
               "97 numeric member access (m.content.0.output) throws inside llama.cpp's "
               "capability probe; the probe is swallowed, supports_object_arguments stays "
               "false, the replayed call's arguments are never decoded, and line 157's "
               "arguments.items() dies on the string -- the turn produces no answer and "
               "the error reaches the chat (replay_http_status 400, replay_parser_error "
               "True, replay_items_error True). AFTER Studio detects the numeric access "
               "and launches with a repaired copy, so the probe passes, the arguments are "
               "decoded, and the same conversation renders an answer "
               "(replay_http_status 400 -> 200, replay_parser_error True -> False, "
               "replay_items_error True -> False). A pair where BEFORE also answers would "
               "mean the local llama.cpp predates the autoparser and cannot show this.",
        kwargs={
            "model_path": "/Users/nilay/.mimir/skills/pr-ui-evidence/temp/ev_glm53/"
                          "models/glm53-template-probe.gguf",
            "context_length": 4096,
        },
        needs_model=True,
        verified="confirmed on base 5fdcf9cc7 vs head 6d17805ea, two isolated installs "
                 "(:8990 / :8991), both on llama.cpp b10672-760fb1c76, same 0.5B GGUF "
                 "carrying the real GLM-5.3 template. Every predeclared fact moved: "
                 "replay_http_status 400 -> 200, replay_parser_error true -> false, "
                 "replay_items_error true -> false, ui_shows_parser_error true -> false, "
                 "ui_body_char_count 1268 -> 894. The composite shows it: BEFORE the "
                 "follow-up turn ends in a red banner reading 'llama-server error: "
                 "code 400, Unable to generate parser for this template ... at line 157' "
                 "with a Retry button, and the header context meter reads '- / 4.1k' "
                 "because /apply-template refused the same messages; AFTER the identical "
                 "thread and follow-up produce a completed assistant turn (Thought for 0 "
                 "seconds, 229.8 tok/s) and the meter reads '113 / 4.1k'. The stand-in "
                 "model's answer text is not meaningful -- a 0.5B under a GLM template -- "
                 "so the pair proves error vs completed turn; the real GLM-5.3 failure was "
                 "captured separately against a live instance.",
    ),
    9891: ScenePlan(
        pr=9891, scene="openai_videos_api_surface",
        what="the generated API docs page Studio serves at /docs, filtered on both sides to "
             "operations whose path contains 'video', plus a live probe of GET /v1/videos and "
             "the /openapi.json behind it. No model is loaded on either side.",
        expect="BEFORE (merge base) Studio serves video only on its own /api/inference/video/* "
               "routes: /openapi.json has no /v1/videos path, the filtered docs panel shows the "
               "native rows only, and GET /v1/videos is 404 with no 'object' "
               "(openapi_v1_videos_operation_count 0, get_v1_videos_status 404). AFTER the "
               "OpenAI videos router is mounted and five operations join the same panel -- POST "
               "/v1/videos, GET /v1/videos, GET /v1/videos/{video_id}, GET "
               "/v1/videos/{video_id}/content, DELETE /v1/videos/{video_id} -- so "
               "operation_count 0 -> 5, get_v1_videos_status 404 -> 200, get_v1_videos_object "
               "None -> 'list'. The router is mounted on both prefixes, so the filtered panel "
               "gains 10 rows (5 on /v1 + 5 on /api/inference): docs_video_rows_shown 19 -> 29. "
               "Control that must NOT move: studio_native_video_op_count -- the pre-existing "
               "/api/inference/video/* operations -- stays equal and non-zero on both sides, and "
               "those rows stay visible in BOTH panels. An empty BEFORE panel or a moved control "
               "would mean the base install broke, not that the PR added a surface.",
        needs_model=False,
        verified="confirmed on base e745043b3 vs head 2ebddefa3, two isolated installs, no "
                 "model loaded. Facts moved exactly as predicted: "
                 "openapi_v1_videos_operation_count 0 -> 5, get_v1_videos_status 404 -> 200, "
                 "get_v1_videos_object null -> \"list\", docs_video_rows_shown 19 -> 29, and "
                 "the control studio_native_video_op_count held at 16 on both sides. The "
                 "composite shows it: BEFORE the inference section carries only the 16 native "
                 "/api/inference/video/* rows and there is no openai-compat section at all; "
                 "AFTER those same 16 rows are unchanged and a new openai-compat section lists "
                 "exactly POST /v1/videos, GET /v1/videos, GET /v1/videos/{video_id}, DELETE "
                 "/v1/videos/{video_id} and GET /v1/videos/{video_id}/content, with the same "
                 "five also mounted under /api/inference/videos. Two scene bugs had to be fixed "
                 "first and both produced a useless pair: a CSS :has() filter matched nothing "
                 "so the panel was the whole unfiltered page, and Swagger lost its own "
                 "/openapi.json fetch on the AFTER side so that half rendered \"Failed to load "
                 "API definition\" -- the load is now retried until an operation row exists. A "
                 "third failure was environmental: a concurrent session cycling Studios through "
                 "the default 8990+ band took the port between the bind probe and launch twice, "
                 "so its Studio answered the login; PR_UI_PORT_BASE=9400 moved this run onto its "
                 "own band.",
    ),
    9813: ScenePlan(
        pr=9813, scene="chat_picker_speech_row",
        what="the chat model picker's on-device list, with a downloaded Orpheus-shaped "
             "voice folder seeded beside an ordinary llama-shaped one -- same "
             "architecture, same files, different vocabulary",
        expect="BEFORE (merge base) the voice folder is chat-capable on architecture alone "
               "(LlamaForCausalLM ends in a generative suffix), so it is listed in the chat "
               "picker and is what auto-load picks -- and the chat route answers a turn on a "
               "speech model by synthesizing the prompt. AFTER the codec vocabulary is read "
               "and the row leaves chat "
               "(voice_row.can_chat True -> False, picker_lists_voice True -> False). "
               "Control that must NOT move: the llama folder stays can_chat True and stays "
               "listed on both sides, or the shot proves only that the list broke.",
        verified="confirmed on base 95fd60fa2 vs head 7ac516895, two isolated installs. Facts "
                 "moved exactly as predicted: voice_row.can_chat true -> false, "
                 "picker_lists_voice true -> false, and the control folder holds at "
                 "can_chat true / listed on both sides. The composite shows it: chat picker "
                 "-> On Device, filtered to \"my-\", BEFORE lists my-chat-llama AND "
                 "my-voice-orpheus, AFTER lists my-chat-llama alone. Two scene bugs had to be "
                 "fixed first and both produced a clean identical pair: the picker opens on "
                 "Recommended (the Hub catalogue) rather than On Device, and even on the right "
                 "tab both seeded folders sort below the fold behind the box\'s ambient cache, "
                 "so inner_text saw the row leave while the viewport did not. The shot is "
                 "clipped to the panel and the seeded absolute path is masked.",
    ),
    9814: ScenePlan(
        pr=9814, scene="clipboard_user_gesture",
        what="the Copy preview link button on a completed run in Studio's History tab, "
             "clicked in WebKit on a Studio that reports a Cloudflare tunnel URL and "
             "answers /api/health in 6s once the app has finished booting. Six seconds is "
             "the ordinary slow-health case, not a contrived one: fetchDeviceType already "
             "polls that endpoint for up to HARDWARE_DETECT_WAIT_MS = 5000ms while the "
             "backend measures hardware. The shot is taken at a fixed 1500ms after the "
             "click. NOTE: Safari's actual clipboard denial is not reproducible under "
             "Playwright's WebKit (a write 9s after the click still resolves there, in "
             "both the Studio origin and a bare page), so this scene photographs the "
             "await that causes the denial, not the denial.",
        expect="BEFORE (merge base) the click awaits fetchDeviceType({force: true}) before "
               "it copies anything: health_calls_after_click 1, click_to_toast_ms ~6300, "
               "and at the 1500ms shot the screen is unchanged -- toast_visible_at_shot "
               "False, no feedback at all. AFTER (PR head plus the review fix) the refresh "
               "has already happened in an effect, so the click copies first: "
               "health_calls_after_click 1 -> 0, click_to_toast_ms ~6300 -> a few hundred, "
               "toast_visible_at_shot False -> True, and the 1500ms frame shows the "
               "\"Preview link copied\" toast. Control that must NOT move: "
               "copied_is_tunnel_link stays True and copied_url stays "
               "https://pr9814-evidence.trycloudflare.com/p/unsloth/Qwen3-1.7B/run-9814"
               "?k=s1gn4tur3-9814 -- dropping the click-time refresh must not cost the "
               "store its tunnel URL, or the button would copy a link only this machine "
               "can open. copy_button_visible stays True, or the click had no target.",
        verified="Confirmed by eye at head 5c1e057375 vs merge base 32ba55b578, exact-SHA "
                 "homes on :8990/:8991 (the AFTER home refused to reuse its 55b04d9a91 "
                 "build and was rebuilt). At the same 1500ms frame BEFORE shows the "
                 "History card untouched -- no toast, no feedback, the click still on "
                 "/api/health -- and AFTER shows \"Preview link copied\" in the top right. "
                 "Every predicted fact moved (health_calls_after_click 1 -> 0, "
                 "click_to_toast_ms 6390 -> 1588, toast_visible_at_shot False -> True) and "
                 "both controls held (copied_is_tunnel_link True on both at the same tunnel "
                 "URL; copy_button_visible True on both). One fact moved that was not "
                 "predicted and is not evidence: health_calls_at_boot 2 -> 34. It counts "
                 "every /api/health from page load to the shot, so on the AFTER side -- a "
                 "Studio launched straight off a fresh install -- it measures how long the "
                 "cold boot took, not behaviour. Measured separately on that build, the "
                 "calls land at 1.09s, 1.11s (app boot), 1.40s (the grid's mount read) and "
                 "then every 15s, which is the poll as written.",
    ),
    9726: ScenePlan(
        pr=9726, scene="deep_research_greeting_arm",
        what="the chat, and the server behind it, right after a user arms Deep Research in "
             "the composer and sends \"hi\" -- with the model itself a browser fixture that "
             "answers the greeting in plain text and calls no tool, so the only thing that "
             "differs between the two shots is the Studio each is talking to",
        expect="A chat gets ONE Deep Research run. BEFORE (merge base) arming it creates that "
               "run before the model reads anything, so the greeting spends it: zero "
               "/v1/chat/completions requests leave the browser, one POST reaches "
               "/api/chat/research-runs, the server reports 1 run for the thread, the reply "
               "is a research card instead of an answer, and the Deep research pill is gone "
               "for the rest of the chat. AFTER (PR head) the model is offered the tool and "
               "declines it for a greeting: completion_requests 0 -> 1 carrying "
               "deep_research_armed true, research_run_posts 1 -> 0, "
               "server_runs_for_greeting 1 -> 0, server_has_run True -> False, "
               "reply_text_visible False -> True, and research_still_offered False -> True. "
               "Controls that must NOT move: armed_before_sending stays True (the pill was "
               "really lit on both sides, or nothing was tested) and thread_id_present stays "
               "True (the same real thread was read on both servers).",
        kwargs={"model": "unsloth/Qwen3-1.7B-GGUF", "variant": "Q4_K_M"},
        verified="Confirmed by eye at head ddf6491149 vs merge base 8194d50fce, exact-SHA homes "
                 "on :8992/:8993. BEFORE: 'hi' produced a Deep research card reading 'Planning / "
                 "Planning an approach' with the activity panel open on 'Research requested' and "
                 "'Investigating your question - building the plan', and the composer had lost "
                 "its Deep research pill (only Approve for me / Search / Code left). AFTER: the "
                 "same 'hi' got the plain reply 'Hey! What would you like to look into?' and the "
                 "Deep research pill was still lit beside Search and Code. Every predicted fact "
                 "moved (completion_requests 0 -> 1 carrying deep_research_armed true, "
                 "research_run_posts 1 -> 0, server_runs_for_greeting 1 -> 0 with statuses "
                 "['planning'] -> [], server_has_run True -> False, research_still_offered "
                 "False -> True) and both controls held (armed_before_sending True on both, one "
                 "real thread read on both servers). One fact is weak and was not part of the "
                 "prediction: research_card_visible stayed True on both, because it substring-"
                 "matches 'esearch', which the composer pill also spells on the AFTER side. thread_count "
                 "moved 3 -> 2 only because the two homes accumulated different numbers of throwaway "
                 "threads across reruns; the scene starts its own chat, which is what is measured.",
    ),
    9722: ScenePlan(
        pr=9722, scene="mlx_bnb_suggestion_rows",
        kwargs={"clip": {"x": 820, "y": 0, "width": 680, "height": 300}},
        what="what Studio tells an Apple Silicon (MLX) user for the ninety-five seconds "
             "after they pick unsloth/Qwen2-VL-2B-Instruct-bnb-4bit from the model "
             "picker, plus what /api/models/list and /api/inference/validate say "
             "about that pick on the same server. The scene parks the base repo out "
             "of the Hub cache first, so both builds face the same uncached 4.43 GB "
             "fetch and can disagree about what they say is being fetched.",
        expect="mlx-lm cannot read bitsandbytes NF4, so the loader silently swaps this "
               "1.56 GB repo for its 4.43 GB base and downloads THAT. BEFORE (PR merge "
               "base) the only toast at 95s is 'Starting model... / Loading cached model "
               "into memory.' -- false twice over, since neither the repo being fetched "
               "nor the fetch itself is what that sentence claims -- so "
               "load_toast_says_cached is True, download_progress_visible False, "
               "mlx_toast_seen False and mlx_loads_base_model None. AFTER (PR head) the "
               "same 95s frame carries two toasts: the notice 'MLX cannot use 4-bit "
               "bitsandbytes weights / Loading unsloth/Qwen2-VL-2B-Instruct instead, "
               "downloading it first if needed.', and a load toast that has become "
               "'Downloading model...' over a bar sized to the BASE repo -- 'of 4.4 GB', not the 1.56 GB of the repo that was picked, which is the retarget by itself. "
               "So load_toast_headline goes 'Starting model...' -> 'Downloading model...', "
               "load_toast_says_cached True -> False, frame_names_base False -> True, "
               "download_progress_visible False -> True, mlx_toast_seen False -> True and "
               "mlx_loads_base_model None -> 'unsloth/Qwen2-VL-2B-Instruct'. The suggestion "
               "list moves with it: bnb_suggestion_count 11 -> 0, with bnb_in_curated (3) "
               "and bnb_in_fetched_ranking (8) both emptying. Controls that must NOT move: "
               "gpu_name stays the same Apple Silicon device, ranking_fetched stays True, "
               "validate_valid stays True, base_repo_was_cached stays True and hovered_to_pause stays True "
               "(the scene, not the network, put both sides in the same cache state, and "
               "the stack was pinned open on both), shot_at_seconds stays "
               "~95 on both, and picked_row stays 'Qwen2-VL-2B-Instruct-bnb-4bit | 2B' -- "
               "the same row reached the same way on both sides.",
        verified="Confirmed by eye at head 065f89fb83 vs merge base 540abc2db9, exact-SHA "
                 "homes on :8992/:8993, same Apple M5 Pro. BEFORE: one toast, 'Starting "
                 "model... / Loading cached model into memory.', still standing 95s into a "
                 "4.43 GB transfer it never mentions. AFTER: two, the notice naming "
                 "unsloth/Qwen2-VL-2B-Instruct and 'Downloading model... / 0.0 of 4.4 GB / "
                 "0%' under a progress bar. Every predicted fact moved "
                 "(bnb_suggestion_count 10 -> 0 with bnb_in_curated 3 -> 0 and "
                 "bnb_in_fetched_ranking 7 -> 0, mlx_loads_base_model None -> the base, "
                 "mlx_toast_seen False -> True, load_toast_headline 'Starting model...' -> "
                 "'Downloading model...', load_toast_says_cached True -> False, "
                 "download_progress_visible False -> True, frame_names_base False -> True) "
                 "and every control held (same gpu_name, ranking_fetched True, "
                 "validate_valid True, picked_row identical, base_repo_was_cached True, "
                 "hovered_to_pause True, shot_at_seconds 95.0 vs 95.01). "
                 "bnb_suggestion_count is 10 not the 11 of the earlier run because the "
                 "unsloth-by-downloads ranking is live; the curated 3 are fixed, the rest "
                 "is whatever the Hub ranks that hour. Two things the pair does NOT show. "
                 "The bar reads 0% because an unauthenticated xet transport had still "
                 "materialised no measurable bytes into the Hub cache the progress endpoint "
                 "measures, at 95s -- the 4.4 GB total is what proves the retarget, not the "
                 "percentage. And a fresh install boots chat-only (mlx_lm fails to import on "
                 "a tokenizers pin) and self-heals about seventy seconds later, so the first "
                 "attempt read gpu_name null and a GGUF curated list on BOTH sides and "
                 "showed no suggestion-list change at all; _settled_hardware now gates on "
                 "the healed state, and this pair was shot on reused, already-healed homes.",
    ),
    9718: ScenePlan(
        pr=9718, scene="rag_attached_document_roster",
        what="what /api/inference/chat/count_tokens -- the endpoint behind the composer's "
             "context meter -- prices for a project chat with two ingested documents, "
             "against a second project with none, on a resident Qwen3-0.6B GGUF",
        expect="tool_block_cost must be large on BOTH sides (>100), or the tool block was "
               "never priced and nothing was measured. The control project (no documents) "
               "must price IDENTICALLY on both sides. On BEFORE the with-documents chat "
               "prices the same as its control, roster_token_cost 0. On AFTER its prompt "
               "carries 'The attached documents are: \"course-syllabus.txt\", "
               "\"hostel-allotment.txt\"' plus the read-them-as-data sentence, so "
               "roster_token_cost goes positive and the meter reads a larger prompt.",
        kwargs={
            # Tool-capable on purpose: count_tokens prices the tool block, the grounding
            # nudge and therefore the roster only when the template declares tools, and
            # gemma-3's does not. Qwen3-0.6B is the smallest that does.
            "model": "unsloth/Qwen3-0.6B-GGUF",
            "variant": "Q4_K_M",
            "context_length": 2048,
        },
        needs_model=True,
        verified="Confirmed by eye at head 7f78c42bc7 vs fresh main 4deb8ad2de, exact-SHA "
                 "homes, Qwen3-0.6B-GGUF Q4_K_M resident on both at a 2,048 window. Control "
                 "project priced 299 on BOTH sides, so the null holds; tool_block_cost 264 "
                 "and 314 (vs 35 with enable_tools false), so the block was really priced. "
                 "The with-documents chat moved 299 -> 349 prompt tokens, meter '299 / 2.0k' "
                 "-> '349 / 2.0k' and tooltip 14.6% -> 17.0%. Three earlier scene revisions "
                 "were discarded: a connected provider never receives this nudge at all "
                 "(external tool loop), count_tokens 503s on a RAG-scoped PENDING turn so the "
                 "conversation must end on an assistant turn, and gemma-3-270m declares no "
                 "tools so the whole block -- nudge and roster included -- went unpriced.",
    ),
    9721: ScenePlan(
        pr=9721, scene="web_fetch_non_ascii_url",
        what="the two web_search cards, and the assistant bubble under them, after a real "
             "tool loop in which the model reads "
             "https://de.wikipedia.org/wiki/K\u00fcnstliche_Intelligenz as typed and then "
             "reads the same page with the umlaut already escaped",
        expect="BEFORE (PR merge base) the URL is handed to http.client as typed, so the "
               "first card's result pane reads \"Failed to fetch URL: 'ascii' codec can't "
               "encode character '\\xfc' in position 11\" (raw_url_failed True, "
               "raw_url_result_len ~110, raw_url_has_page_title False) while the SECOND "
               "card -- the same page, pre-escaped -- comes back with the article "
               "(escaped_url_failed False). AFTER (PR head) both cards carry the article "
               "and raw_url_failed goes True -> False with raw_url_result_len jumping into "
               "the tens of thousands and raw_url_has_page_title False -> True. The escaped "
               "URL is the control and must succeed on BOTH sides: it is what rules out a "
               "network or Wikipedia difference between the two runs.",
        verified="CONFIRMED on two isolated installs, base 4deb8ad2d vs head c9c6fb240, each "
                 "stamped with its own SHA. Every predicted key moved and nothing else did: "
                 "raw_url_failed true -> false, raw_url_result_len 201 -> 16037, "
                 "raw_url_has_page_title false -> true, page_shows_fetch_failure true -> false, "
                 "raw_url_result_head \"Failed to fetch URL: 'ascii' codec can't encode "
                 "character '\\xfc' in position 11\" -> \"# K\u00fcnstliche Intelligenz aus "
                 "Wikipedia, der freien Enzyklop\u00e4die\". The control held: "
                 "escaped_url_result_len is 16037 on BOTH sides, byte for byte, so the two runs "
                 "reached the same Wikipedia revision and only the spelling of the URL decided "
                 "whether it arrived. Both sides also agree web_search_offered_to_model=true, "
                 "provider_completions=3, approvals_clicked=2 and tool_group_label='2 tool "
                 "calls', so the tool loop ran identically and only the fetch differs. The "
                 "composite reads plainly: on the left the first Read card is the ascii-codec "
                 "error and the second (same page, pre-escaped) is the article; on the right "
                 "both cards are the article. Base was pinned explicitly because the local "
                 "clone's origin is the fork, whose main is 5610 commits diverged from upstream "
                 "and resolves a nonsense merge base; 4deb8ad2d IS this PR's own merge base "
                 "against fresh upstream main.",
    ),

    9639: ScenePlan(
        pr=9639, scene="thread_scoped_settings_localid",
        what="two chats seeded through the real chat-history API under ids of the shape "
             "assistant-ui mints for a chat started in the app (`__LOCALID_<id>`), plus two "
             "real forks of A's reply: the composer pills after Search is switched on inside "
             "Chat A, the untouched Chat B, and the fork badge on A's reply",
        expect="BEFORE (PR merge base) the `__LOCALID_` prefix is read as \"this chat has no "
               "row yet\", so the edit made inside Chat A never pairs to its row: it moves the "
               "installation defaults instead (installation_default_moved False -> True is the "
               "BEFORE state), chat_a_stored_settings stays null, untouched Chat B opens with "
               "Search already ON (chat_b_search_pill_active 'true'), and the fork badge is "
               "absent because the store skips the fetch. AFTER (PR head) the edit lands on "
               "Chat A's own row (chat_a_stored_tools_enabled null -> True), the installation "
               "defaults are untouched, Chat B opens with Search OFF, and the badge reads 2. "
               "forks_api_counts must be identical on both sides -- the backend already agreed.",
        verified="CONFIRMED on two isolated installs, base 32ba55b57 vs head cddc874f4 (the only "
                 "later commit on the PR is a pre-commit.ci reformat of a Python harness file, "
                 "which the browser never loads). Every predicted key moved: chat_a_stored_settings "
                 "null -> a full snapshot with toolsEnabled true, chat_b_search_pill_active 'true' "
                 "-> 'false', installation_default_moved true -> false, fork_badge_text null -> '2'. "
                 "forks_api_counts is {'pr9639-a-reply': 2} on BOTH sides, which is the control: the "
                 "backend always knew about the two forks and only the UI refused to ask. The "
                 "composites show it plainly -- pair_01 has the Search pill lit green in the "
                 "NEVER-EDITED Chat B on the left and grey on the right, which is the leak itself, "
                 "and pair_02 has the fork badge in the reply's action bar only on the right. "
                 "Base was pinned explicitly because the local clone's origin is the fork and its "
                 "stale origin/main resolved an older base (2599f9fe9) than the PR's real merge "
                 "base; 32ba55b57 IS the PR's own merge base against fresh upstream main.",
    ),

    9636: ScenePlan(
        pr=9636, scene="mcp_embedded_resource_image",
        kwargs={"mimes": ("image/png", "application/png")},
        what="the chat tool card for a stdio MCP tool whose result carries its images as "
             "EmbeddedResource/BlobResourceContents blocks -- one labelled image/png and one "
             "labelled application/png, the mime FastMCP's File(data=..., format='png') emits "
             "-- driven through the real tool loop",
        expect="BEFORE (PR merge base) the backend drops both embedded-resource blocks, so the "
               "model receives an empty tool result and the card's result pane shows no text "
               "and no image; AFTER (PR head) both render and the model is handed "
               "'[2 images attached; displayed to the user]' "
               "(result_image_count 0 -> 2, tool_result_text_len 0 -> non-zero)",
        verified="confirmed on base 785a68dc4 vs head 29186b970, two isolated installs stamped "
                 "785a68dc4/29186b970. Facts moved exactly as predicted: tool_result_text "
                 "\"\" -> \"[2 images attached; displayed to the user]\", tool_result_text_len "
                 "0 -> 42, result_image_count 0 -> 2, result_image_dims [] -> [[192,192],"
                 "[192,192]]. Both sides agree tool_offered_to_model=true and "
                 "provider_completions=2, so the tool ran identically and only the flattening "
                 "differs. The composite shows the symptom plainly: BOTH sides end with the "
                 "model saying \"Here is the image I generated with the local ComfyUI graph\", "
                 "and on BEFORE the result pane reads \"Result:\" and stops with no image. "
                 "result_image_count 2 (not 1) is what proves the application/png half: the "
                 "image/png block alone renders on the PR's first three commits, the "
                 "application/png block needs the _image_mime normalisation in 29186b970.",
    ),

    9637: ScenePlan(
        pr=9637, scene="audio_archive_shelf",
        what="the Audio page's clip row menu, and Settings -> Data's archive shelves, "
             "with two clips seeded straight into <home>/audio as WAV + sidecar pairs",
        expect="BEFORE the row menu holds four items (Use text again / Copy text / "
               "Download WAV / Delete) with no Archive, Settings -> Data lists only "
               "Archived images and Archived videos, and PATCH /api/inference/audio/"
               "gallery/<id> is 405 because the route does not exist. AFTER the menu "
               "holds five with Archive above the separator, Data gains an Archived "
               "audio row, and the PATCH answers 200 and moves the clip off History "
               "(menu_item_count 4 -> 5, has_archive_item False -> True, "
               "has_archived_audio_row False -> True, patch_flags_status 405 -> 200, "
               "history_after_archive 2 -> 1, archived_shelf_count 0 -> 1)",
        verified="CONFIRMED on two isolated installs (base 099865689, head 130e7cd37). Every "
                 "predicted key moved: menu_item_count 4 -> 5, has_archive_item False -> True, "
                 "has_archived_audio_row False -> True, patch_flags_status 405 -> 200, "
                 "history_after_archive 2 -> 1. Both composites show it: pair_00 has Archive "
                 "between Download WAV and Delete only on the right, pair_01 has an Archived "
                 "audio row under Archived videos only on the right. ONE key moved differently "
                 "than predicted: archived_shelf_count is 2 -> 1, not 0 -> 1, because BEFORE "
                 "has no `archived` query param and silently ignores it, so ?archived=true "
                 "returns the whole of History rather than an empty shelf. That is a stronger "
                 "result than predicted, not a weaker one.",
    ),
    9638: ScenePlan(
        pr=9638, scene="vision_latest_image",
        what="the chat transcript of a two-image vision thread, at the second answer",
        expect="BEFORE answers ONE on the second turn -- the model is still being shown "
               "the image that opened the thread -- while AFTER answers TWO, the image the "
               "user had just attached. turn2_words_named moves ['ONE'] -> ['TWO'] and "
               "turn2_reads_newest_image moves False -> True.",
        kwargs={"model": "unsloth/Qwen2-VL-2B-Instruct"},
        needs_model=True,
        verified="Ran on Qwen2-VL-2B-Instruct (MLX, vision), same 318-token prompt both "
                 "sides. Turn 1 is the control and matches: ONE attached, 'ONE' answered on "
                 "both. Turn 2 with TWO attached answered 'ONE' on BEFORE and 'Two' on AFTER, "
                 "so image_reaching_model_turn2 read 'ONE (first attachment)' -> 'TWO (newest "
                 "attachment)'. Two earlier scene revisions were discarded first: the "
                 "composer has no standing file input (the + menu opens a real chooser) and "
                 "this checkpoint answers 'White' for any solid colour field, so the images "
                 "carry a rendered word instead.",
    ),
    9641: ScenePlan(
        pr=9641, scene="tool_arg_non_string",
        what="a stored thread reopened from history, whose model answered `python` with "
             "{\"code\": 42} and `code_execution` with {\"command\": 42}",
        expect="BEFORE the first card calls .split on a number, the throw reaches the "
               "router boundary and the whole of Studio is replaced with \"Something went "
               "wrong!\" with no cards on screen; AFTER both cards render and show the "
               "argument as text "
               "(app_crashed True -> False, tool_cards_rendered 0 -> 2)",
        verified="confirmed on base 785a68dc4 (the PR's own merge base, and current "
                 "upstream main) vs head de19960ed. Facts moved as predicted: app_crashed "
                 "true -> false, crash_banner \"Something went wrong\" -> null, "
                 "tool_cards_rendered 0 -> 2, body_char_count 32 -> 618. The composite "
                 "shows it: BEFORE is a blank page carrying only \"Something went wrong!\" "
                 "and a Show Error button, with no sidebar, no thread list and no chat; "
                 "AFTER is the whole of Studio with the seeded thread open and both cards "
                 "drawn -- \"Used tool: Python: 42\" over a script cell reading 42, and "
                 "\"Used tool: Ran `42`\" for code_execution. Same seeded bytes on both "
                 "sides (stored_message_count 2), so the build is the only variable.",
    ),
    9635: ScenePlan(
        pr=9635, scene="image_generation_preview",
        what="the Images viewer photographed mid-denoise, with a Z-Image-Turbo GGUF loaded "
             "and steps cranked so the run lasts long enough to shoot",
        expect="BEFORE the viewer is empty behind a percentage bar and the progress payload "
               "carries no `preview` key, so 0 distinct frames arrive during the denoise. "
               "AFTER the viewer holds a blurry latent thumbnail, `preview` is a "
               "data:image/jpeg;base64 URL, and several DISTINCT frames arrive while the run "
               "is still active.",
        kwargs={"repo": "unsloth/Z-Image-Turbo-GGUF",
                "filename": "z-image-turbo-Q4_K_S.gguf"},
        needs_model=True,
    ),
    9433: ScenePlan(
        pr=9433, scene="local_speech_gguf_picker",
        what="the chat model picker's on-device list, with a seeded llama-csm GGUF folder "
             "beside a runnable llama one",
        expect="BEFORE the csm folder classifies text-generation and is offered in the chat "
               "picker, so a pick reaches llama-server which cannot load it; AFTER it "
               "classifies text-to-speech and the arch-task gate keeps it out of chat, while "
               "the qwen3 folder stays listed on both sides "
               "(speech_folder_task text-generation -> text-to-speech, "
               "picker_lists_speech_folder True -> False)",
        verified="PARTIAL. The server fact moves as predicted on two isolated installs "
                 "(speech_folder_task text-generation -> text-to-speech, runnable qwen3 folder "
                 "unchanged at text-generation). The VISUAL half was not obtained: the chat "
                 "picker opens (panel text 1188 chars both sides) but lists NEITHER seeded "
                 "custom-scan folder, including the runnable control, so the row cannot be shown "
                 "appearing then disappearing. Model hub / On Device does list both folders but "
                 "renders no capability, so that pair is identical too. Do not publish either "
                 "composite as evidence.",
    ),
    9301: ScenePlan(
        pr=9301, scene="mcp_app_widget_card",
        what="the tool card for a stored MCP Apps result, reopened from history",
        expect="BEFORE serialises the result and prints the ui:// resource id as text, "
               "with no iframe; AFTER draws the ui:// template in a sandboxed widget "
               "frame above the collapsible (widget_iframe_count 0 -> 1)",
        verified="confirmed on base 2599f9fe9 vs head 22f57938b. Facts moved as predicted: "
                 "widget_iframe_count 0 -> 1, widget_rendered false -> true, "
                 "ui_resource_requests 0 -> 1, card_shows_raw_resource_uri true -> false, and "
                 "card_text drops the serialised envelope for the tool's own text. The "
                 "composite shows it: BEFORE prints the whole {text, ui:{resourceUri, content, "
                 "structuredContent}} blob as the result body, AFTER draws a Weather card "
                 "(18C / 72% / 14 km/h) with a Refresh button above the collapsible. "
                 "widget_body_text reads null -> \"\" rather than the card's text because the "
                 "frame is opaque-origin and its body is not readable from the host; the "
                 "iframe count and the screenshot are what carry the visual half.",
    ),
    10556: ScenePlan(
        pr=10556, scene="gguf_picker_rows",
        what="Model Hub quant list for a repo publishing two builds at one quant",
        expect="4 rows -> 8 rows; the row labelled Q4_K_M fetches Hy3-Q4_K_M-mtp.gguf "
               "before and Hy3-Q4_K_M.gguf after, and the four plain builds become "
               "selectable for the first time",
        kwargs={"repo": "AngelSlim/Hy3-GGUF",
                # Fixed clip on the detail pane, as 8255 uses: the rows are small type
                # and a full-width viewport renders at ~440 px per half in a comment.
                "clip": {"x": 780, "y": 150, "width": 700, "height": 620}},
    ),
    8222: ScenePlan(
        pr=8222, scene="gguf_picker_rows",
        what="Model Hub quant list for a multi-checkpoint GGUF repo",
        expect="22 rows -> 63 rows; BF16 126 GB -> three 42 GB rows "
               "(BF16, BF16 · distilled, BF16 · distilled-1.1)",
        kwargs={"repo": "unsloth/LTX-2.3-GGUF"},
        verified="confirmed at head 69f54b51 on a clean two-install run: 22 -> 63 rows, "
                 "BF16 126 GB -> three 42 GB rows, Q8_0 68 GB -> 23 GB, and the bare "
                 "BF16 (dev) row selectable for the first time. API agrees: 117.45 GiB "
                 "-> 39.15 GiB. Side effect worth knowing: the DEFAULT selection also "
                 "changes (BF16 -> Q4_K_M · distilled-1.1), since the default resolver "
                 "now picks among real checkpoints. IDEMPOTENCE measured on full row "
                 "identity (key+label+size+filename, not row counts): flat, sharded and "
                 "quant-named-subdirectory repos are byte-for-byte unchanged; the mildly "
                 "affected ones keep their keys and only correct the size. The one "
                 "migration effect is that LTX-2.3-GGUF's surviving bare keys repoint "
                 "from distilled-1.1 to the dev checkpoint",
    ),
    8255: ScenePlan(
        pr=8255, scene="gguf_picker_rows",
        what="Model Hub quant list for a repo publishing one quant at several bit widths",
        expect="4 rows -> 18 rows, each at its own true size",
        kwargs={"repo": "byteshape/Llama-3.1-8B-Instruct-GGUF",
                # Fixed clip on the detail pane: the rows are 12 px type, and a
                # full 1500 px viewport renders at ~440 px per half in a comment.
                "clip": {"x": 780, "y": 150, "width": 700, "height": 620}},
        verified="confirmed by eye at head 4b470d414 vs merge base 871a088b5 (the base is "
                 "8222's branch `ggufrows`, NOT main -- this PR is stacked, so BEFORE "
                 "already contains 8222's family narrowing and the pair isolates the bpw "
                 "key alone). Picker: 4 rows -> 18 rows. BEFORE rows and the sizes the "
                 "picker advertised: Q4_K_S 16 GB, IQ4_XS 12 GB, Q3_K_S 16 GB, IQ3_S 18 GB "
                 "-- those are the SUMS of the 4/3/5/6 files sharing each token (Hub "
                 "listing: 16.0/11.6/16.1/17.7 GB), while the row itself resolved to one "
                 "file, Q4_K_S -> ...-Q4_K_S-3.60bpw.gguf at 3.63 GB. So BEFORE advertises "
                 "16 GB for a 3.6 GB download and the other 14 checkpoints are "
                 "unreachable. AFTER every file is its own row at its own size, 4.33 GB "
                 "down to 2.56 GB, and /api/models/gguf-variants agrees exactly with the "
                 "Hub listing on all 18. NOTE the summed size is computed in the FRONTEND: "
                 "the BEFORE backend endpoint already returns the single-file size "
                 "(3.63 GB) while the picker draws 16 GB, so the two disagree until this "
                 "PR keys them the same way. "
                 "GOTCHAS this run hit, both of which produced BYTE-IDENTICAL halves on "
                 "the first attempt (md5 equal, and the shot was of "
                 "YorkieOH10/Meta-Llama-3.1-8B-Instruct-Q8_0-GGUF). (1) a search RESULT ROW "
                 "is also a <button>, and on this query most results are named after a "
                 "quant (`...-Q8_0-GGUF`), so open_list's bare quant-token match hit the "
                 "results list and SELECTED an unrelated repo; the scene now filters the "
                 "selector with has_not_text on row chrome (relative date, download count). "
                 "(2) assert_showing passed on the wrong page: the results column is headed "
                 "'Results for \"byteshape/Llama-3.1-8B-Instruct-GGUF\"', which contains the "
                 "leaf, so the assertion was satisfied by the query typed rather than by "
                 "anything selected; the scene now asserts an EXACT heading plus the owner "
                 "line. Also: unsloth and byteshape both publish a repo named "
                 "Llama-3.1-8B-Instruct-GGUF and the owner-scoped results land before the "
                 "global ones, so `get_by_text(leaf).first` picked whichever had loaded -- "
                 "rows are now matched by owner, and Playwright's has_text normalises "
                 "whitespace so a `^owner$` row filter never matches",
    ),
    # MERGED 2026-08-09. Shot afterwards, at the merge base vs the merged head.
    8241: ScenePlan(
        pr=8241, scene="diffusion_quant_badge",
        what="loaded-models row for a GGUF pick the dense fast path replaced with a "
             "torchao int8 build",
        # Rewritten after reading the NET diff. The original expect ("BF16 -> Q8_0") was
        # taken from the PR title and from `gh pr diff --patch`, which replays the whole
        # commit series: an early commit added a `gguf_quant` field that a later one
        # dropped for the `gguf_variant` already on main. A plain GGUF load therefore
        # reads "GGUF · Q4_K_M" on BOTH sides and proves nothing.
        expect="loading unsloth/Z-Image-Turbo-GGUF Q4_K_M with transformer_quant=int8: "
               "'Image · z-image · GGUF · Q4_K_M · cuda' -> 'Image · z-image · INT8 · cuda'. "
               "The GGUF token goes because the pipeline never opened that file, and the "
               "precision becomes the dense build that actually ran. Same weights, same "
               "load, ONLY the label",
        kwargs={"repo": "unsloth/Z-Image-Turbo-GGUF",
                "filename": "z-image-turbo-Q4_K_M.gguf",
                "transformer_quant": "int8"},
        needs_model=True,
        verified="confirmed by eye at merged head 3c400936f vs merge base 8e558606a. Both "
                 "sides load identically (model_kind=gguf, gguf_variant=Q4_K_M, "
                 "transformer_quant=int8, dtype=bfloat16, device=cuda); the row reads "
                 "'Image · z-image · GGUF · Q4_K_M · c...' BEFORE (truncated by the 268 px "
                 "panel, which is itself the point: the width went on a filename nothing "
                 "opened) and 'Image · z-image · INT8 · cuda' AFTER. "
                 "GOTCHAS: (1) `gh pr diff --patch` replays the COMMIT SERIES, not the net "
                 "diff -- it showed a `gguf_quant` field this PR does not ship. Use "
                 "`git diff <base> <head>`. (2) /images/load returns in ~2 s with "
                 "loaded=false and finishes later, so status must be polled. (3) the whole "
                 "load, weights cached, takes about 10 s on this box",
    ),
    8219: ScenePlan(
        pr=8219, scene="image_generation_stop",
        what="Images composer action row during a generation, and again just after Stop",
        expect="two pairs on a 50-step batch-of-4 Z-Image run. (0) mid-run: BEFORE offers no "
               "way to stop it, AFTER shows Stop in place of Generate. (1) eight seconds "
               "later: AFTER is back to Generate with generate-progress active=false, BEFORE "
               "is still running. API: POST /images/generate/cancel 404 on the base, 200 on "
               "the head",
        kwargs={"repo": "unsloth/Z-Image-Turbo-GGUF",
                "filename": "z-image-turbo-Q4_K_M.gguf"},
        needs_model=True,
        verified="confirmed by eye at head 58c8c4642 vs merge base 8e558606a. pair_00 mid-run: "
                 "BEFORE a disabled spinner reading Generate, AFTER a live Stop. pair_01: "
                 "BEFORE still a disabled spinner (active=true at step 19 after 60 s), AFTER "
                 "back to a live green Generate with active=false. cancel route 405 -> 200. "
                 "GOTCHAS, three, and the middle one nearly shipped a wrong claim. "
                 "(1) get_by_role('button', name='Stop', exact=True) matches NOTHING here even "
                 "though inner_text is exactly 'Stop' -- the icon contributes to the accessible "
                 "name. Filter on text. That failure was silent: the click was skipped and the "
                 "pair simply showed two running sides. (2) cancellation latency is batch "
                 "dependent, measured: ~20 s at batch 4, under 4 s at batch 1. The original "
                 "fixed 8 s wait photographed AFTER still on Stop, which reads as 'the button "
                 "does nothing'. Poll to inactive instead. (3) BEFORE's Steps slider maxes at "
                 "100 and AFTER's at 50, so the two runs are not the same length; it does not "
                 "affect this claim but do not read it as a difference this PR made",
    ),
    8223: ScenePlan(
        pr=8223, scene="companion_assets_panel",
        what="Hub On Device tab: toolbar, and the delete dialog for an image GGUF and for "
             "the companion base repo its quants share (issue 8116)",
        expect="three differences on one cache holding unsloth/FLUX.2-klein-4B-GGUF "
               "(Q2_K + Q4_K_M, 4.4 GB) and the companion base "
               "black-forest-labs/FLUX.2-klein-4B (8.2 GB of text encoders, VAE, "
               "tokenizer). (0) toolbar: no way to reclaim shared assets -> a 'Free up "
               "space' control. (1) deleting the GGUF repo: 'You can re-download it later.' "
               "and nothing else -> plus 'Frees 4.4 GB of disk space.' and 'This also "
               "leaves 8.2 GB of shared assets (black-forest-labs/FLUX.2-klein-4B) that "
               "nothing else needs. Remove them with Free up space on the On Device tab.' "
               "(2) deleting the shared base while a quant is installed: the same plain "
               "dialog with Delete ENABLED (it succeeds, stranding both quants) -> a red "
               "'These are shared assets that unsloth/FLUX.2-klein-4B-GGUF still needs, so "
               "they cannot be removed yet. Delete those models first.' with Delete "
               "DISABLED. API: /api/hub/delete-impact and /api/hub/orphan-companions 404 "
               "on the base and answer on the head",
        kwargs={"gguf_repo": "unsloth/FLUX.2-klein-4B-GGUF",
                "base_repo": "black-forest-labs/FLUX.2-klein-4B"},
        verified="confirmed by eye at head aacfb9c0f vs merge base f0ef75ec9, two installs, "
                 "one seeded cache (scripts/seed_8223_cache.py: 4,432,118,912 B of Q2_K + "
                 "Q4_K_M and 8,229,021,460 B of companion base, real blobs). pair_00 "
                 "toolbar: 'Free up space' appears between Add folder and All formats. "
                 "pair_01 GGUF delete: BEFORE ends at 'You can re-download it later.'; AFTER "
                 "adds 'Frees 4.4 GB of disk space.' and the 8.2 GB orphan sentence. pair_02 "
                 "base delete: BEFORE identical wording with Delete ENABLED; AFTER shows the "
                 "red refusal naming unsloth/FLUX.2-klein-4B-GGUF with Delete greyed out. "
                 "API: delete-impact 405 / orphan-companions 404 on the base; on the head "
                 "reclaimed_bytes 4,432,118,912 with the base as freeable at 8,229,021,460, "
                 "and blocked_by=['unsloth/FLUX.2-klein-4B-GGUF'] for the base. "
                 "GOTCHA for later PRs: this box sets XDG_CACHE_HOME to a SHARED HF cache of "
                 "~26 repos which Studio scans alongside HF_HOME, so an unisolated run "
                 "photographs other sessions' downloads and its byte counts move under it. "
                 "Pass all four of HF_HOME/HF_HUB_CACHE/HF_XET_CACHE/XDG_CACHE_HOME through "
                 "--studio-env",
    ),
    8224: ScenePlan(
        pr=8224, scene="image_memory_plan",
        what="Images page after asking for 2048x2048 on a device that cannot hold the "
             "activations (GPU 2 held down to about 14 GiB free by scripts/gpu_ballast.py)",
        expect="BEFORE the generation is started and dies in the allocator, so the page shows "
               "a bare failure. AFTER it is refused before any work with the arithmetic: "
               "'Generating at 2048x2048 needs about 29.20 GB of working memory (including "
               "about 2.00 GB of fixed overhead), but only about 0.00 GB is usable on this "
               "device (of the 13.59 GB currently free...)'. 1024x1024 succeeds on both sides, "
               "which is the control: the claim is that the refusal replaces a crash, not that "
               "big requests are blocked",
        kwargs={"repo": "unsloth/Z-Image-Turbo-GGUF",
                "filename": "z-image-turbo-Q4_K_M.gguf",
                "width": 2048, "height": 2048},
        needs_model=True,
        verified="RAN, and it did NOT show what `expect` predicted -- it showed the opposite, "
                 "which is the point of writing `expect` first. At head 67cbde473 vs merge base "
                 "f0ef75ec9, both sides offload_policy=model, free 15.2 GiB (BEFORE) vs 13.9 GiB "
                 "(AFTER): BEFORE GENERATED the 2048x2048 image, AFTER refused it claiming "
                 "29.20 GB of working memory against '0.00 GB usable of the 13.87 GB currently "
                 "free'. Measured the truth on the base build by sampling nvidia-smi every "
                 "0.4 s: free 17,304 MiB before the call, 8,564 MiB at the trough, so peak "
                 "working memory about 8.7 GB, and it returned a 2048x2048 image. The estimate "
                 "is ~3x high and refuses work that succeeds. Reported on the PR. "
                 "GOTCHA that nearly invalidated the first run: this box SHARES GPUs, and "
                 "another session released ~17 GiB between the two sides, so they measured "
                 "different devices. gpu_ballast.py now tops up while holding (never gives "
                 "back), and the scene records free_mib per side. Without both, 'one refused, "
                 "one succeeded' says nothing about the PR",
    ),
    8213: ScenePlan(
        pr=8213, scene="unified_memory_refusal",
        what="load refusal on a host that cannot fit the model in unified memory",
        expect="load proceeds and is OS-killed -> a refusal naming the shortfall",
    ),
    8232: ScenePlan(
        pr=8232, scene="gguf_download_plan",
        what="download manager panel while staging a GGUF pick, on the companion base item",
        expect="picking unsloth/Qwen-Image-2512-GGUF Q4_K_M stages the base repo at 58 GB "
               "-> about 17 GB, because the 11 dense transformer/ shards the GGUF replaces "
               "are no longer fetched. API plan total 66.08 GiB -> 28.02 GiB, transformer "
               "file count 11 -> 0. NOTE: the 'GGUF · BF16' label is 8241's bug, NOT this "
               "PR's; do not read a label change here as evidence for 8232",
        kwargs={"repo": "unsloth/Qwen-Image-2512-GGUF",
                "base_repo": "unsloth/Qwen-Image-2512",
                "filename": "qwen-image-2512-Q4_K_M.gguf",
                "quant": "Q4_K_M",
                # Both sides share one cache (--studio-env is not per side), so the scene
                # purges these two repos from it before each run. Without that the AFTER
                # total would be smaller because BEFORE already downloaded, not because of
                # the fix.
                "cache_hub": "outputs/ui_diff_8232/hf_cache/hub"},
        verified="confirmed by eye at head 02b77383e vs merge base f0ef75ec9, two installs, one "
                 "purged-per-side cache. pair_00 (the download panel, element shot): both halves "
                 "show unsloth/Qwen-Image-2512-GGUF as Downloaded, then the base item "
                 "unsloth/Qwen-Image-2512 at '4.3 KB / 58 GB' BEFORE and '357 MB / 17 GB' AFTER. "
                 "API plan: total 66.08 -> 28.02 GiB; the GGUF entry is 12.34 GiB on both sides "
                 "(unchanged, as it must be), and the base entry goes 53.74 GiB / 28 files / 11 "
                 "transformer shards -> 15.69 GiB / 17 files / 0. "
                 "GOTCHA: the panel shows ONE item at a time, so the shot has to wait for the "
                 "SECOND item; base_repo is a strict prefix of repo, so the match needs the "
                 "trailing separator or it fires on the GGUF row, which is identical on both "
                 "sides. Cost per side: the 13 GB GGUF downloads first (~20 s at 750 MB/s), then "
                 "the scene cancels the base transfer a few seconds in",
    ),
    # MERGED 2026-08-09. Shot afterwards, at the merge base vs the merged head.
    8196: ScenePlan(
        pr=8196, scene="video_family_train_picker",
        what="Images -> Train tab, the 'Model family' Select and the panel under it",
        # Written from the NET diff (git diff <merge-base> <head>), not from the PR body:
        # the body's "Deliberately left out / The Train UI listing" describes an EARLIER
        # commit. The merged head rewrites family_train_infos() to walk
        # _all_trainable_family_names() -- the image registry's trainable families UNION
        # TRAINABLE_VIDEO_FAMILIES -- so the listing does gain the video family.
        expect="the Model family dropdown gains one row, 'LTX-2', appended after the "
               "image families (mergeFamilies puts a backend family the frontend has no "
               "preset for last). Picking it, which is impossible on the BEFORE side, "
               "sets Base model to Lightricks/LTX-2 and shows the chips '19B' and "
               "'QLoRA 36GB+ VRAM' with the note 'Video: trains a style LoRA on still "
               "images.'. API /api/train/diffusion/info: families gains an ltx-2 entry "
               "with defaults rank 32 / lr 1e-4 / resolution 512 and deploy_base null. "
               "CAVEAT to check in the facts, not to assume: the head also drops any "
               "family whose pipeline class the installed diffusers lacks, so LTX-2 only "
               "appears where diffusers has LTX2Pipeline (0.39+)",
        verified="confirmed by eye at merged head 18dc63502 vs merge base f9656fd6c, two "
                 "installs, diffusers 0.39.0 with LTX2Pipeline present on BOTH sides (so the "
                 "availability filter is not what makes the difference). Model family menu: 7 "
                 "options -> 8, BEFORE flux.1 / flux.2-klein / flux.2-dev / qwen-image / "
                 "z-image / krea-2 / sdxl, AFTER the same seven plus ltx-2 appended last. "
                 "Picking it: Base model Lightricks/LTX-2, chips '19B' + 'QLoRA 36GB+ VRAM', "
                 "note 'Video: trains a style LoRA on still images.', defaults rank 32 / "
                 "lr 1e-4 / 512px / 20 warmup, deploy_base null (a video run publishes no "
                 "image LoRA catalog entry, so there is nothing to deploy). "
                 "CORRECTS AN EARLIER READ in this session that the feature was unreachable "
                 "from the Train tab. That came from the PR BODY, whose 'Deliberately left "
                 "out / The Train UI listing' paragraph describes an earlier commit; the net "
                 "diff at the merged head rewrites family_train_infos() to walk "
                 "_all_trainable_family_names() and the row is there. Read the net diff, not "
                 "the description, even on a merged PR",
    ),
    8244: ScenePlan(
        pr=8244, scene="video_family_train_picker",
        what="Images -> Train tab, the 'Model family' Select and the panel under it",
        # Written from the NET diff (git diff 749437314 70bdc2ceb), not the PR body. The
        # reachable-by-hand surface of this PR is one listing: TRAINABLE_VIDEO_FAMILIES
        # goes {ltx-2} -> {ltx-2, minimax-h3}, which is what _all_trainable_family_names()
        # feeds family_train_infos() and therefore GET /api/train/diffusion/info, which is
        # what the Select is built from. Everything else the PR ships (the H3 trainer, the
        # clip dataset layer, the packed-sequence layout, the H3 inference paths) is behind
        # that row.
        expect="the Model family dropdown gains exactly one row, 'MiniMax-H3': 8 options "
               "-> 9. mergeFamilies appends any backend family the frontend has no preset "
               "for, in backend order, and the video registry lists minimax-h3 before "
               "ltx-2, so the new row lands next to LTX-2 among the appended ones. Picking "
               "it, which is impossible on the BEFORE side, sets Base model to "
               "MiniMaxAI/MiniMax-H3 and shows the chips '31B' and 'QLoRA 72GB+ VRAM' with "
               "the note 'Video with sound: trains on clips that have a soundtrack.'. API "
               "/api/train/diffusion/info: families gains a minimax-h3 entry with defaults "
               "rank 16 / lr 1e-4 / resolution 768 / 20 warmup, deploy_base null, and "
               "precision_modes EMPTY (minimax-h3 is not in _DIT_TRAIN_FAMILIES, so /info "
               "advertises no base-precision list for it and recommended_precision is nf4). "
               "CAVEAT to read from the facts rather than assume: family_train_infos drops "
               "a family whose pipeline class the installed diffusers lacks, and H3's is "
               "ModularPipeline, so the row only appears where diffusers exposes it. "
               "RE-RUN AT THE LIVE HEAD (f73ac79e4): the precision_modes clause above is "
               "now OUT OF DATE and is the point of the re-run. The empty list was a real "
               "bug -- family_train_infos read _DIT_TRAIN_FAMILIES for is_dit while the "
               "rest of the PR had moved to _FLOW_TRAIN_FAMILIES -- and it is fixed at this "
               "head, so the pair must now show precision_modes NON-EMPTY for minimax-h3 "
               "and the floating Start control reading 'Start training' and ENABLED after "
               "picking it. A shot that still says 'Not supported on this GPU' means the "
               "AFTER home was built from a stale SHA, not that the fix is absent. "
               "RE-RUN 2026-08-10 (second) WITH OVERRIDDEN REFS AND A SEEDED CLIP FOLDER. "
               "This PR's own merge base can no longer show its effect: head f73ac79e4 "
               "withholds a clip-trained family until a listed dataset reports clips, and "
               "the clip_count field that makes any folder able to report clips is added by "
               "a DIFFERENT open PR, #8287. So the honest pair is BEFORE = 8287 alone "
               "(--base-ref tmp8287) and AFTER = 8287 + 8244 (--head-ref evcomb), with "
               "seed_clips=3 putting the SAME image folder and clip folder in both homes. "
               "Expect: BEFORE 8 options, no MiniMax-H3, target_in_api false; AFTER 9 "
               "options with MiniMax-H3 appended beside LTX-2, target_in_api true, picking "
               "it sets base MiniMaxAI/MiniMax-H3, and the floating Start control reads "
               "'Start training' and is ENABLED (precision_modes non-empty, the fix "
               "described above). The comment MUST say the refs are not 8244's merge base "
               "and why.",
        kwargs={"target": "MiniMax-H3", "family_key": "minimax-h3",
                "pipeline_attr": "ModularPipeline",
                # Taller than 8196's default crop and started past the sidebar: it reaches
                # the foot of the settings column, so the SAME frame carries the family the
                # PR adds and the state of the floating Start control for it. Without the
                # button in shot the pair answers "is it listed" and leaves "can it be
                # started" -- which is where this PR actually fails -- out of frame.
                "clip": {"x": 288, "y": 66, "width": 416, "height": 934},
                # Seeded on BOTH sides. Head f73ac79e4 withholds a clip-trained family
                # until some listed dataset reports clips, so with a stills-only home the
                # AFTER side hides minimax-h3 too and the pair goes identical for a reason
                # that has nothing to do with this PR. The seed is the same folder on both
                # halves, so the family list is still the only thing that differs.
                "seed_clips": 3},
        verified="confirmed by eye at head 70bdc2ceb vs merge base 749437314, two installs, "
                 "both reused on an exact .uidiff_sha match, both on GPU 2 (B200), login "
                 "identity verified per side (BEFORE :8996, AFTER :8997 from the driver's own "
                 "lines). ModularPipeline present on BOTH sides, so the availability filter is "
                 "not what makes the difference (diffusers 0.39.0 BEFORE, 0.40.0.dev0 AFTER -- "
                 "the PR moves the pin; noted because it is a second difference between the "
                 "sides, but it does not gate this row). "
                 "pair_00 menu: 8 options -> 9, MiniMax-H3 appended between Krea 2 and LTX-2, "
                 "exactly the backend order predicted. pair_01 panel: BEFORE stays on the "
                 "FLUX.1-dev default (there is no H3 row to click), AFTER reads MiniMax-H3 with "
                 "chips '31B' + 'QLoRA 72GB+ VRAM', base MiniMaxAI/MiniMax-H3 and the note "
                 "'Video with sound: trains on clips that have a soundtrack.'. So `expect` is "
                 "MATCHED on every clause, including precision_modes [] and "
                 "recommended_precision nf4. "
                 "BUT `expect` stopped one clause short of the thing that decides whether the "
                 "feature ships usable, and the answer is NO. That empty precision_modes is not "
                 "inert: the panel computes familyUntrainable = isDiT && "
                 "precision_modes.length === 0, where isDiT is merely `familyName !== \"sdxl\"`. "
                 "So [] on MiniMax-H3 disables the Start control and labels it 'Not supported on "
                 "this GPU'. Measured on the AFTER build, same Studio process, same B200, "
                 "seconds apart (scripts/h3_8244_start_gate.py): LTX-2 -> 'Start training', "
                 "enabled, precision_modes [nf4,bf16,int8,fp8,mxfp8,auto]; MiniMax-H3 -> 'Not "
                 "supported on this GPU', DISABLED, precision_modes []. Same host, so the "
                 "button's own wording is false. "
                 "ROOT CAUSE, from the net diff: the PR adds _FLOW_TRAIN_FAMILIES = "
                 "_DIT_TRAIN_FAMILIES | {minimax-h3} and switches three call sites to it "
                 "(bf16_unsupported_reason, dit_accelerator_missing_reason, "
                 "training_precision_preflight_error) but NOT the fourth, family_train_infos's "
                 "`is_dit = name in _DIT_TRAIN_FAMILIES`, which is what drives precision_modes. "
                 "The PR touches zero files under studio/frontend/src/features/images/train/. "
                 "THE TRAINER ITSELF IS FINE, which is why this is a listing bug and not a "
                 "feature failure: driving POST /api/train/diffusion/start directly on the same "
                 "build ran a real 20-step H3 LoRA to completion on 3 clips of 1280x720 24-frame "
                 "video WITH AAC stereo audio -- loss 0.415 -> 0.533 (avg 0.683), 0.447 img/s, "
                 "77.76 GB peak, and 332,674,080 B of adapter, 600 tensors over 200 modules at "
                 "rank 16 (lora_A [16,5376] / lora_B [7168,16] on the shared transformer_blocks "
                 "stack, no separate audio/video towers, as the PR's own comment describes). "
                 "GOTCHA: pair_01's two halves differ in BOTH family and button state, so it "
                 "alone cannot support the button claim -- a reader can answer 'different "
                 "families, of course'. The LTX-2 vs MiniMax-H3 shot on ONE build is the "
                 "controlled version and is the image that carries the finding. "
                 "RE-RUN 2026-08-10 at head 256a98a8d vs merge base 587590d6a, both sides "
                 "rebuilt (the stamps for 70bdc2ceb/749437314 no longer matched), login "
                 "identity verified per side, BEFORE :8998 AFTER :8999. The pair is now "
                 "IDENTICAL -- 8 options on both, target_in_api false on both -- and that is a "
                 "RESULT, not a trap. Head commit f73ac79e4 'Withhold a clip-trained family "
                 "from the Train picker until a clip dataset is listable' added "
                 "routes/training.py::_ui_trainable_families, which drops CLIP_TRAINED_FAMILIES "
                 "from /diffusion/info whenever no listed dataset reports clips; "
                 "DiffusionDatasetSummary carries no clip_count field at all yet, so every "
                 "folder answers 0 and minimax-h3 is withheld on every host. ModularPipeline is "
                 "True on both sides, so the availability filter is not the cause. The earlier "
                 "precision_modes finding was FIXED in the meantime (family_train_infos now "
                 "reads _FLOW_TRAIN_FAMILIES for is_dit) and is no longer observable through the "
                 "picker, because the row is not offered at all. "
                 "Trainer proof moved off the picker accordingly: "
                 "scripts/h3_8244_live_train_head.py starts the run through POST "
                 "/diffusion/start (which the gate still accepts by design) on the AFTER build "
                 "and photographs the Train tab mid-run. Completed 20/20, loss 0.415 -> 0.532 "
                 "(avg 0.681), 0.43 img/s, 77.76 GB peak, adapter 332,675,760 B / 600 tensors / "
                 "200 modules at rank 16. Composite: "
                 "outputs/ui_diff_8244/combined/pr8244_h3_trainer_at_head.png (NOT posted as an "
                 "image: its third pane shows the absolute on-disk adapter path). "
                 "MATCHED on the gated re-run, --base-ref tmp8287 (437c1ebe6) --head-ref evcomb "
                 "(631190074), both installs fresh, login identity verified per side, BEFORE "
                 ":9000 AFTER :9003, diffusers 0.40.0.dev0 with ModularPipeline True on both. "
                 "8 -> 9 options, MiniMax-H3 between Krea 2 and LTX-2; target_in_api false -> "
                 "true; the panel picks through to base MiniMaxAI/MiniMax-H3 with chips '31B' + "
                 "'QLoRA 80GB+ VRAM' and the soundtrack note; precision_modes "
                 "[nf4,bf16,int8,auto], recommended auto, and Start reads 'Start training' "
                 "ENABLED on BOTH sides -- so the pair no longer carries the button finding, "
                 "which is now fixed. The seeded dataset row reads 'clip-style - 3 clips' "
                 "identically on both halves, which is what makes the family list the only "
                 "moving part. Posted at "
                 "https://github.com/unslothai/unsloth/pull/8244#issuecomment-5237711835 with "
                 "the ref override stated in the comment itself.",
    ),
    8287: ScenePlan(
        pr=8287, scene="clip_dataset_picker",
        what="Images -> Train, the 'Training images' dataset picker, with an image "
             "folder and a clip folder seeded into the same datasets root",
        expect="BEFORE lists only 'photo-style - 3 images'; the clip folder is invisible "
               "because /diffusion/info admits a folder only on image_count > 0. AFTER "
               "lists BOTH, with 'clip-style - 3 clips' selectable, and picking it leaves "
               "the trigger showing clip-style. The image row must be IDENTICAL on both "
               "halves: if it is not, the panel failed to load and the pair proves nothing.",
        verified="MATCHED on a clean two-install run (base b063387cc, head accdf20fd). Same "
                 "seed on both homes: 3 ffmpeg-encoded MP4s with AAC in clip-style, 3 PNGs in "
                 "photo-style, a .txt beside every file. BEFORE /info returned 1 dataset "
                 "(photo-style, image_count 3, clip_count NULL -- the field does not exist on "
                 "that build) and the menu had 7 options, none of them the clip folder. AFTER "
                 "returned 2 (clip-style image_count 0 / clip_count 3 / caption_count 3, "
                 "photo-style 3/0/3) and the menu had 8, with 'clip-style - 3 clips' at the "
                 "top; dataset_after_pick went from 'photo-style - 3 images' to 'clip-style - "
                 "3 clips'. The control held: the photo-style row is identical on both halves. "
                 "Second pair is worth reading too -- the AFTER panel correctly drops the "
                 "thumbnail strip and the 'Review captions' toggle for a clip-only dataset, "
                 "both of which go through the image thumbnail endpoint.",
    ),
    8267: ScenePlan(
        pr=8267, scene="image_train_base_picker",
        what="Images -> Train, the FLUX.2 Klein family's 'Base model' Select and the "
             "FamilyFacts chips above it",
        # Written from the NET diff (git diff f7ea9fab6 <head>), not the PR body. Two
        # coupled changes: train_base_repos goes from the single distilled
        # black-forest-labs/FLUX.2-klein-4B to the two UNDISTILLED bases base-4B and
        # base-9B, and _BASE_TRAIN_SPECS overlays params 9B / qlora_vram_gb 18 on the 9B
        # one so it stops inheriting the family's 4B / 10 GB floor. FamilyFacts takes
        # baseModel, so the chips are a function of the base pick, not just the family.
        expect="the Base model dropdown under FLUX.2 Klein goes from 2 options "
               "(black-forest-labs/FLUX.2-klein-4B plus 'Custom repo or local path...') "
               "to 3 (FLUX.2-klein-base-4B, FLUX.2-klein-base-9B, Custom). Picking the "
               "9B row, which is impossible on the BEFORE side, moves the chips from "
               "'4B' / 'QLoRA 10GB+ VRAM' to '9B' / 'QLoRA 18GB+ VRAM'. API "
               "/api/train/diffusion/info: the flux.2-klein entry's base_repos goes 1 -> "
               "2, default_base changes from the distilled 4B to base-4B, and base_specs "
               "gains an entry keyed on black-forest-labs/flux.2-klein-base-9b with "
               "params 9B and qlora_vram_gb 18. THE TRAP HERE: the family-level params "
               "and qlora_vram_gb stay 4B / 10 on both sides, so a shot that photographs "
               "the chips WITHOUT picking the 9B base is two identical halves that look "
               "like a passing run. The base pick is the whole point.",
        verified="MATCHED by eye on a clean two-install run, base f7ea9fab6 vs head "
                 "0cd220171. Base menu 2 options -> 3: BEFORE "
                 "['black-forest-labs/FLUX.2-klein-4B', 'Custom repo or local path...'], "
                 "AFTER ['black-forest-labs/FLUX.2-klein-base-4B', "
                 "'black-forest-labs/FLUX.2-klein-base-9B', 'Custom...']. target_in_menu "
                 "False -> True, base_after_pick FLUX.2-klein-4B -> FLUX.2-klein-base-9B, "
                 "chip_texts ['4B'] -> ['9B'], and the panel reads 'QLoRA 10GB+ VRAM' -> "
                 "'QLoRA 18GB+ VRAM'. API: base_repos 1 -> 2, default_base distilled 4B -> "
                 "base-4B, base_specs null -> the 9B base and its unsloth mirror at params "
                 "9B / qlora_vram_gb 18, deploy_bases null -> 4 entries pairing each "
                 "training base to its inference base, vendor and mirror ids alike. "
                 "The predicted trap held exactly: family-level params and qlora_vram_gb "
                 "read 4B / 10 on BOTH sides, so the chip difference exists only after the "
                 "9B row is clicked. "
                 "SCENE GOTCHAS for whoever reuses image_train_base_picker. (1) "
                 "assert_showing waits on a HEADING, and the family name lives in a Select "
                 "trigger, so calling it here would have passed vacuously while the family "
                 "select sat on whatever it defaulted to; the scene asserts the trigger's "
                 "own inner_text instead. (2) the chip regex catches only the params chip "
                 "('4B'/'9B'), not 'QLoRA 18GB+ VRAM', so the VRAM figure comes from the "
                 "API facts and the screenshot rather than chip_texts. (3) the scene prints "
                 "facts truncated to 900 chars, which cut base_after_pick and chip_texts "
                 "off the AFTER line and briefly read like a missed click; the full facts "
                 "are in outputs/ui_diff_8267/meta.json, which is what to check.",
    ),
    8700: ScenePlan(
        pr=8700,
        scene="api_monitor_throughput",
        what="API monitor request-detail timing metrics for one completed llama.cpp request",
        expect="with the same deterministic request carrying prompt_tok_per_sec=2048.4 and "
               "tok_per_sec=64.2, BEFORE shows only 'Speed 64.2 tok/s'; AFTER replaces it "
               "with separate 'Prompt speed 2048 tok/s' and 'Generation speed 64.2 tok/s' "
               "metrics, so prefill and decode throughput are both visible without conflation",
        verified="confirmed by eye at head 049016b97 vs merge base 99bcfd3d9 with two "
                 "isolated installs and Chromium 141 at 1500x1000. The same fixed request "
                 "renders SPEED 64.2 tok/s BEFORE and PROMPT SPEED 2048 tok/s plus "
                 "GENERATION SPEED 64.2 tok/s AFTER. The detail grid remains readable with "
                 "no clipping or overlap; image bytes and the label facts differ.",

    ),

    8882: ScenePlan(
        pr=8882,
        scene="context_window_header",
        what="Chat header with a deterministic resident local GGUF and no measured usage",
        expect="with the same resident unsloth/gemma-3-270m-it-GGUF Q4_K_M fixture at a "
               "32,768-token window, Deep Research armed, and zero /chat/count_tokens requests "
               "on both sides: BEFORE has no context control; AFTER shows '— / 32.8k' with "
               "aria-label 'Context window: 32.8k tokens, usage not counted yet' and no fill",
        kwargs={
            "model": "unsloth/gemma-3-270m-it-GGUF",
            "variant": "Q4_K_M",
            "context_length": 32768,
        },

        verified="confirmed by eye at head 511f262bf vs merge base 203007d19 with exact-SHA "
                 "isolated installs and Chromium 151 at 1500x900. Both sides adopted the same "
                 "gemma-3-270m-it-GGUF Q4_K_M fixture at context_length 32768, Deep Research "
                 "was persisted true, and /chat/count_tokens requests stayed 0. BEFORE had no "
                 "context control; AFTER visibly showed '— / 32.8k', exposed the matching "
                 "usage-not-counted aria-label, and drew no fill.",
    ),

    9489: ScenePlan(
        pr=9489,
        scene="cancelled_turn_history",
        what="Chat thread after Stop-before-any-output, then re-sending the same prompt",
        expect="with the same resident unsloth/gemma-3-270m-it-GGUF Q4_K_M fixture and the same "
               "three actions (send, Stop before a token, send again): BEFORE the second request "
               "carries two user turns in a row and the strict local template refuses it, so the "
               "thread shows 'conversation roles must alternate'; AFTER the second request "
               "carries one user turn and the thread shows the model's reply",
        kwargs={
            "model": "unsloth/gemma-3-270m-it-GGUF",
            "variant": "Q4_K_M",
            "context_length": 32768,
        },
        verified="confirmed by eye at head afb218c7e vs merge base 067e2ffc8 with exact-SHA "
                 "isolated installs and Chromium at 1280x900. Both sides ran the same three "
                 "actions against the same gemma-3-270m-it-GGUF Q4_K_M fixture and both show "
                 "the same stopped turn and the same re-sent prompt. BEFORE posted "
                 "[user, assistant(''), user] and the strict template refused it: the thread "
                 "shows 'Jinja Exception: ... conversation roles must alternate' plus a "
                 "'Generation failed' toast, and no reply. AFTER posted [user] alone and the "
                 "thread shows the model's haiku. Facts moved on second_request_user_turns "
                 "(2 -> 1), backend_verdict (template-refused -> answered), "
                 "template_error_visible (true -> false) and reply_visible (false -> true).",
    ),

    9173: ScenePlan(
        pr=9173,
        scene="mmproj_low_vram",
        what="Chat composer after attaching an image to PaddleOCR under constrained VRAM",
        expect="with the same reported PaddleOCR GGUF + mmproj pair and GPU 0 held near "
               "1.8 GiB free: BEFORE silently reloads text-only, status reports is_vision=false "
               "with no fallback reason, and attaching the image shows the misleading "
               "'cannot accept images' error; AFTER retries the projector on CPU, status "
               "reports is_vision=true and mmproj_fallback_reason=cpu_offload, and the same "
               "image remains attached in the composer without that error",
        kwargs={"model_file": "PaddleOCR-VL-1.6-GGUF.gguf"},
        needs_model=True,
    ),

    9129: ScenePlan(
        pr=9129,
        scene="project_chat_run_keepalive",
        what="A project chat's answer, after the user opens another chat mid-response",
        expect="with the same 40-token fixture answer and the same in-app sidebar click "
               "away at ~token 5: BEFORE the fixture is aborted by the navigation and "
               "stops short of 40, the chat comes back truncated with 'Response stopped.'; "
               "AFTER the same navigation leaves the run attached, the fixture delivers all "
               "40 tokens, and the returned chat ends on w40 with no stopped notice",
        kwargs={
            "model": "unsloth/gemma-3-270m-it-GGUF",
            "variant": "Q4_K_M",
            "context_length": 32768,
            "chunk_ms": 300,
        },
    ),

    9584: ScenePlan(
        pr=9584, scene="mcp_embedded_resource_image",
        what="the chat tool card for a stdio MCP tool whose result carries its image as an "
             "EmbeddedResource (BlobResourceContents), driven through the real tool loop",
        expect="BEFORE the backend drops the embedded-resource block entirely, so the model "
               "is handed an empty tool result and the card's result pane shows no text and "
               "no image; AFTER the same call yields '[1 image attached; displayed to the "
               "user]' and the generated PNG renders in the card "
               "(result_image_count 0 -> 1, tool_result_text_len 0 -> non-zero)",
        verified="confirmed on base 099865689 vs head 871ad34e5, two isolated installs, "
                 "refs pinned (this is NOT a PR merge base: there is no PR, and 9584 is an "
                 "issue). Facts moved exactly as predicted: tool_result_text \"\" -> "
                 "\"[1 image attached; displayed to the user]\", tool_result_text_len 0 -> 41, "
                 "result_image_count 0 -> 1, result_image_dims [] -> [[192, 192]]. Both sides "
                 "agree on tool_offered_to_model=true and provider_completions=2, so the tool "
                 "really ran twice over and only the flattening differs. The composite shows "
                 "the user-visible symptom well: BOTH sides end with the model saying \"Here "
                 "is the image I generated with the local ComfyUI graph\", and on BEFORE there "
                 "is no image anywhere -- the result pane reads \"Result:\" and stops. "
                 "SCOPE: this proves the EmbeddedResource fix. It is not evidence about issue "
                 "9584: comfy-mcp returns ImageContent, which both sides already render.",
    ),


    9719: ScenePlan(
        pr=9719, scene="vision_multi_image_message",
        what="the chat outcome of ONE message carrying two images, on a safetensors/MLX "
             "vision model",
        expect="with both ONE and TWO attached to the same turn (attachments_on_turn 2 on "
               "both sides): BEFORE the request is accepted and the model answers naming "
               "ONE only, because the backend forwarded the first image and discarded the "
               "second without saying so; AFTER the same turn is refused with 'This model "
               "takes one image per message...' and no answer appears. refused moves "
               "False -> True, assistant_answered True -> False, and "
               "silently_dropped_an_image True -> False.",
        kwargs={"model": "unsloth/Qwen2-VL-2B-Instruct"},
        needs_model=True,
    ),
    9894: ScenePlan(
        pr=9894, scene="canvas_network_blocked_alert",
        what="the notice an HTML canvas shows when its one CDN script is refused by the "
             "preview frame's CSP, with 'Allow canvas network access' left at its "
             "default (off) on both sides",
        expect="BEFORE (merge base) the canvas answers with a one-line strip along the "
               "BOTTOM naming the count and host and offering exactly one button, 'Allow "
               "for this canvas' -- nothing says a setting is responsible: "
               "uses_alert_component False, banner_at_top False, banner_title '', "
               "banner_button_count 1, has_open_settings_button False, "
               "has_dismiss_button False, mentions_the_setting_by_name False. AFTER the "
               "notice moves to the TOP as a titled alert: uses_alert_component "
               "False -> True, banner_at_top False -> True, banner_title '' -> 'Canvas "
               "network access is off', banner_button_count 1 -> 3, "
               "has_open_settings_button False -> True, has_dismiss_button "
               "False -> True, mentions_the_setting_by_name False -> True, and "
               "banner_char_count rises. Controls that must NOT move: blocked_host stays "
               "cdn.jsdelivr.net, banner_names_host stays True, "
               "has_allow_for_canvas_button stays True and network_setting_enabled stays "
               "False on both sides -- otherwise the shot proves the canvas broke, not "
               "that the message improved.",
        verified="confirmed on base e745043b3 vs head 569c2242f, two isolated installs. Every "
                 "predicted key moved: uses_alert_component false -> true, banner_at_top "
                 "false -> true (centre_fraction 0.919 -> 0.233), banner_title '' -> 'Canvas "
                 "network access is off', banner_button_count 1 -> 3, has_open_settings_button "
                 "and has_dismiss_button false -> true, mentions_the_setting_by_name "
                 "false -> true, banner_char_count 72 -> 247. Controls held on both sides: "
                 "blocked_host cdn.jsdelivr.net, banner_present true, banner_names_host true, "
                 "has_allow_for_canvas_button true, network_setting_enabled false. The composite "
                 "shows it: BEFORE a thin strip along the bottom reading 'Blocked 1 external "
                 "resource from cdn.jsdelivr.net.' with one button and no hint that a setting is "
                 "responsible; AFTER an amber titled alert at the top naming the setting and "
                 "where to change it, with Open Settings, Allow for this canvas, and a dismiss "
                 "X. One scene bug had to be fixed first, and it produced exactly the "
                 "clean-but-empty BEFORE this file warns about: the surface header ends in an "
                 "'absolute inset-x-0 bottom-0 h-px' divider, so a document-order querySelector "
                 "over the whole section matched THAT rather than the banner and reported the "
                 "merge base as showing no notice at all. Lookups are scoped to the iframe's "
                 "wrapper now. Observed but not filed: the head alert covers the canvas heading "
                 "while it is up, which is what the dismiss button is there for.",
    ),

    9892: ScenePlan(
        pr=9892, scene="openai_models_chat_picker",
        what="Settings -> API, the OpenAI usage examples, on a Studio holding two downloaded "
             "GGUFs: an image model (unsloth/Z-Image-Turbo-GGUF) downloaded most recently and a "
             "chat model (unsloth/SmolLM2-135M-Instruct-GGUF) downloaded earlier, nothing "
             "resident. The catalog sorts newest-first, so the image model is the entry the "
             "panel reaches for -- the ordinary case of 'I just pulled an image model'",
        expect="BEFORE (merge base) GET /v1/models carries no task field, so the image GGUF is "
               "advertised exactly like a chat model and, being the newest download, is the one "
               "the examples name: image_repo_task None, image_repo_offered_for_chat True, "
               "chat_picker_row_count 2, usage_example_model_ids "
               "['unsloth/Z-Image-Turbo-GGUF:Q4_K_S'] -- a runnable curl posting an image model "
               "to /v1/chat/completions, which llama.cpp cannot serve. AFTER it is classified and "
               "filtered out of the chat list, so the examples fall through to the real chat "
               "model: image_repo_task None -> 'text-to-image', image_repo_offered_for_chat "
               "True -> False, chat_picker_row_count 2 -> 1, usage_example_model_ids -> "
               "['unsloth/SmolLM2-135M-Instruct-GGUF:Q2_K']. Controls that must NOT move: "
               "v1_models_row_count stays 2 and image_repo_listed_at_all stays True (the image "
               "model must still be discoverable under its real task, not dropped), and "
               "auto_switch_enabled stays True (with it off the panel names nothing either way). "
               "Both sides must render a populated example block -- an empty AFTER would read as "
               "the panel breaking rather than as the picker correcting itself.",
        kwargs={"expect_repo": "unsloth/Z-Image-Turbo-GGUF"},
        verified="confirmed on base 32ba55b57 vs head 775a66ec5, two isolated installs, HOME and "
                 "every HF root pointed at a private cache holding the image model (mtime set "
                 "newest) beside SmolLM2-135M-Instruct-GGUF. Facts moved exactly as predicted: "
                 "usage_example_model_ids [unsloth/Z-Image-Turbo-GGUF:Q4_K_S] -> "
                 "[unsloth/SmolLM2-135M-Instruct-GGUF:Q2_K], image_repo_task null -> "
                 "text-to-image, image_repo_offered_for_chat true -> false, "
                 "chat_picker_row_count 2 -> 1, and all three controls held (v1_models_row_count "
                 "2 both sides, image_repo_listed_at_all true, auto_switch_enabled true). The "
                 "composite shows both sides rendering a full example block with one line "
                 "differing: the curl body names the image model BEFORE and the chat model "
                 "AFTER. An earlier cut of this scene used a box holding ONLY the image model; "
                 "it was rejected as misleading -- AFTER rendered the empty \"no model to name "
                 "yet\" state, which reads as the panel breaking, and it hid that a second id "
                 "left the listing. Ordering is the whole trick: routes/models.py sorts the "
                 "catalog by updated_at DESC, so the image model must be the most recent "
                 "download to be the one the panel reaches for; with a chat model first, both "
                 "sides are identical and the scene proves nothing. Do not seed media into "
                 "./models for this scene -- those get an absolute-path id, which _build_index "
                 "refuses, so the model vanishes from the listing entirely and confounds the "
                 "shot.",
    ),

    10162: ScenePlan(
        pr=10162, scene="chat_message_edit_transcript",
        kwargs={"mode": "edit_tool_order"},
        what="an assistant reply of [text, tool-call, text] opened with 'Edit response' "
             "and saved without changing a character, then reloaded",
        expect="BEFORE (merge base) the editor is seeded with the text parts only, so the "
               "rebuild puts all the prose in front of the surviving tool part: "
               "stored_reply_part_types ['text','tool-call','text'] -> "
               "['text','tool-call'], the two sentences merge into one block on screen and "
               "the web_search card drops below them. AFTER (head) a numbered TOOL marker "
               "records where the card sat, so the order round-trips: "
               "stored_reply_part_types stays ['text','tool-call','text'], "
               "card_still_between_the_two_sentences False -> True, "
               "prose_merged_on_screen True -> False. Controls that must NOT move: "
               "seeded_row_count stays 2 on both sides and 'tool-call' is present in "
               "stored_reply_part_types either way -- the call is never lost, only moved.",
        verified="confirmed on merge base 5c8c238e6 vs head 4c569e298, two isolated "
                 "installs on :9620/:9621 (the default 8990 band was held by another "
                 "session's Studio, so PR_UI_PORT_BASE=9620), Chromium 1280x900. Every "
                 "predicted key moved and nothing else did: stored_reply_part_types "
                 "['text','tool-call'] -> ['text','tool-call','text'], "
                 "prose_merged_on_screen true -> false, "
                 "card_still_between_the_two_sentences false -> true. Controls held: "
                 "seeded_row_count 2 and transcript_before_edit identical on both sides, "
                 "and the tool-call survives either way with its toolCallId, args, "
                 "argsText and result untouched -- BEFORE only merges the prose and drops "
                 "the card to the end. The composite reads plainly: BEFORE both sentences "
                 "sit together with the 'Used tool: Searched ...' card below them; AFTER "
                 "the card is back between 'Let me search the web for that.' and "
                 "'Search finished -- here is what it returned.'",
    ),

    10163: ScenePlan(
        pr=10163, scene="chat_message_edit_transcript",
        kwargs={"mode": "root_branches"},
        what="the chat transcript of a thread whose FIRST user message was edited: two "
             "roots (abandoned prompt + its reply, edited prompt + its reply) seeded "
             "byte-identically through the real chat-history API, then rendered after a "
             "fresh load. No model is loaded -- the scene writes every row itself",
        expect="BEFORE (merge base 5c8c238e6) the history adapter reads a stored parentId "
               "of null as 'legacy record, no parent recorded' and chains the second root "
               "to the reply before it, so all four messages render as ONE linear "
               "conversation and no branch picker is drawn: rendered_bubbles 4, "
               "branch_picker_count 0, abandoned_branch_on_screen True, "
               "both_branches_rendered_as_one_conversation True. AFTER (head) an explicit "
               "null is honoured as a root once the thread has recorded a real parent -- "
               "which the abandoned reply does -- so only the edited branch renders and "
               "the picker returns: rendered_bubbles 4 -> 2, branch_picker_count 0 -> >=1, "
               "abandoned_branch_on_screen True -> False, "
               "both_branches_rendered_as_one_conversation True -> False. Controls that "
               "must NOT move: seeded_row_count stays 4 on both sides (nothing is deleted, "
               "only re-parented at read time) and edited_branch_on_screen stays True "
               "(the edited branch is shown either way).",
        verified="confirmed on merge base 5c8c238e6 vs head 7ed62fee3, two isolated "
                 "installs on :9800/:9801 (the 8990/9620/9700 bands were held by other "
                 "sessions), Chromium 1280x900, no model loaded. Every predicted key "
                 "moved and nothing else did: rendered_bubbles 4 -> 2, "
                 "branch_picker_count 0 -> 1, abandoned_branch_on_screen true -> false, "
                 "both_branches_rendered_as_one_conversation true -> false. Controls "
                 "held: seeded_row_count 4 and seeded_parent_ids "
                 "[null, a-user, null, b-user] identical on both sides, and "
                 "selected_branch_on_screen true either way. The composite reads plainly: "
                 "BEFORE runs 'Write me a haiku about winter.' / 'ABANDONED BRANCH ...' / "
                 "'Write me a haiku about summer.' / 'SELECTED BRANCH ...' together as one "
                 "thread with no picker; AFTER shows only 'Write me a haiku about summer.' "
                 "and its reply, with the 2/2 branch picker back above it.",
    ),

    10161: ScenePlan(
        pr=10161, scene="chat_message_edit_transcript",
        kwargs={"mode": "edit_metadata"},
        what="an assistant reply that stopped on Max Tokens, opened with the pencil and "
             "saved without changing a character, then reloaded",
        expect="BEFORE (merge base 5c8c238e6) updateThreadMessage PUTs a hand-built body "
               "carrying only id/threadId/parentId/role/content/createdAt, and the route "
               "upserts metadata_json from it, so everything else stored with the turn is "
               "written away as SQL NULL: after the reload the 'Response hit the Max Tokens "
               "limit' notice and its Continue button are gone and the speed/timing line "
               "with them. AFTER (head) the save sends the whole record, so only the text "
               "changes. Keys: notice_after_reload 0 -> 1, "
               "continue_button_after_reload 0 -> 1, "
               "max_tokens_notice_survived_the_edit False -> True, "
               "stored_reply_metadata_is_null True -> False, stored_reply_metadata null -> "
               "the seeded incomplete/contextTruncation/timing/contextUsage object. "
               "Controls that must NOT move: notice_before_edit is 1 on BOTH sides (the "
               "seeded thread renders identically until the save), seeded_row_count stays "
               "2, and stored_reply_part_types stays ['text'] -- the text is never lost, "
               "only what was stored beside it. Note this mode seeds a plain reply, so it "
               "does not exercise the generation-run ownership strip that the fix commit "
               "adds; that path is covered by the fork A/B run and by the unit tests.",
    ),

    # ---- local branches against upstream/main @ 74661077e (no PR numbers yet) ----
    900012: ScenePlan(
        pr=900012, scene="chat_message_edit_transcript",
        kwargs={"mode": "root_branches"},
        what="the chat transcript of a thread whose FIRST user message was edited: two "
             "roots (abandoned prompt + its reply, edited prompt + its reply) seeded "
             "through the real chat-history API, then rendered after a fresh load",
        expect="BEFORE (upstream/main) the history adapter reads a stored parentId of "
               "null as 'legacy record, no parent recorded' and chains the second root "
               "to the reply before it, so all four messages render as ONE linear "
               "conversation and no branch picker is drawn: rendered_bubbles 4, "
               "branch_picker_count 0, abandoned_branch_on_screen True, "
               "both_branches_rendered_as_one_conversation True. AFTER the explicit null "
               "is honoured as a root, so only the edited branch renders and the picker "
               "returns: rendered_bubbles 4 -> 2, branch_picker_count 0 -> >=1, "
               "abandoned_branch_on_screen True -> False, "
               "both_branches_rendered_as_one_conversation True -> False. Controls that "
               "must NOT move: seeded_row_count stays 4 and seeded_parent_ids stays "
               "[None,'a-user',None,'b-user'] on both sides, so the rows are identical "
               "and only the build reading them differs; selected_branch_on_screen stays "
               "True (the edited branch is shown either way).",
    ),
    900013: ScenePlan(
        pr=900013, scene="chat_message_edit_transcript",
        kwargs={"mode": "edit_metadata"},
        what="an assistant reply that stopped on Max Tokens, edited with the pencil and "
             "saved unchanged, then reloaded",
        expect="BEFORE (upstream/main) updateThreadMessage PUTs a hand-built body with no "
               "metadata and the route upserts metadata_json from it, writing SQL NULL: "
               "after the reload the 'Response hit the Max Tokens limit' notice and the "
               "Continue button are gone. stored_reply_metadata_is_null False -> stays "
               "False after the fix; on BEFORE it becomes True. Keys: "
               "notice_after_reload 0 (BEFORE) -> 1 (AFTER), "
               "continue_button_after_reload 0 -> 1, "
               "max_tokens_notice_survived_the_edit False -> True, "
               "stored_reply_metadata null -> the seeded incomplete/timing/contextUsage "
               "object. Controls: notice_before_edit is 1 on BOTH sides (the seed renders "
               "identically until the save), and stored_reply_part_types stays ['text'].",
    ),
    900017: ScenePlan(
        pr=900017, scene="chat_message_edit_transcript",
        kwargs={"mode": "edit_tool_order"},
        what="an assistant reply of [text, tool-call, text] edited with the pencil and "
             "saved without changing a character, then reloaded",
        expect="BEFORE (upstream/main) the editor is seeded with text/reasoning parts "
               "only, so the rebuild collects the prose in front of the surviving tool "
               "part: stored_reply_part_types ['text','tool-call','text'] -> "
               "['text','tool-call'], the two sentences merge into one block and the tool "
               "card drops to the end. AFTER a TOOL placeholder records where the card "
               "sat, so the order round-trips: stored_reply_part_types stays "
               "['text','tool-call','text'], card_still_between_the_two_sentences False "
               "-> True, prose_merged_on_screen True -> False. Controls: "
               "seeded_row_count stays 2 on both sides, and the tool-call part survives in "
               "stored_reply_part_types either way -- the call is never lost, only moved.",
    ),
    10164: ScenePlan(
        pr=10164, scene="rag_scope_on_hosted_turn",
        what="the POST body the built bundle sends to /v1/chat/completions for a chat "
             "whose model is an external connection and whose Docs pill is on, "
             "intercepted in the browser and answered with a canned stream",
        expect="BEFORE (merge base) the external branch fills rag_scope.context_length "
               "from `runtime.loadedContextLength ?? params.maxSeqLength`, with no "
               "external guard, so a request that never touches a local model still "
               "carries a local window: context_length_sent True and "
               "rag_scope_context_length is a number. AFTER the guard makes it undefined "
               "for an external request and JSON.stringify drops the key: "
               "context_length_sent True -> False, rag_scope_context_length <number> -> "
               "None. Controls that must NOT move: requests_captured stays 1, "
               "rag_scope_present stays True and rag_scope_keys keeps thread_id / "
               "default_top_k / mode / autoinject on both sides -- the scope itself is "
               "still sent, only the window is dropped.",
        verified="Ran merge base 5c8c238e6 vs head d8b22f1e0. Both panels show the same "
                 "chat, the same prompt and the same canned reply, and each carries the "
                 "rag_scope it actually posted: BEFORE ends "
                 "\"autoinject_min_score\": 0.7, \"context_length\": 4096; AFTER ends "
                 "\"autoinject_min_score\": 0.7 and has no context_length line. Facts: "
                 "context_length_sent True -> False, rag_scope_context_length 4096 -> "
                 "None. Controls held -- requests_captured 1, rag_scope_present True, and "
                 "thread_id / default_top_k / mode / autoinject identical on both sides.",
    ),
    10664: ScenePlan(
        pr=10664, scene="tool_timeout_partial_output",
        what="the chat after a real `terminal` call ran `echo progress; sleep 300` under "
             "the composer's own one-minute Max Tool Call Duration, driven end to end "
             "through the real backend against a saved OpenAI-compatible connection "
             "served from the scene process, so no weights, GPU or real provider is "
             "involved. The measurement is the tool message the loop handed back: the "
             "command had already printed `progress` on stdout, and the drain had "
             "captured it, when the wall-clock limit fired.",
        expect="BEFORE (merge base) the timeout arm returns the status line alone and the "
               "captured stdout is discarded: partial_output_reached_model false, "
               "card_shows_printed_output false, tool_message_len 37, and the assistant "
               "reads 'Studio handed me nothing it printed (37 chars) ...'. AFTER (head) "
               "the captured text is kept above the status line: "
               "partial_output_reached_model false -> true, card_shows_printed_output "
               "false -> true, tool_message_len 37 -> 46, and the assistant reads "
               "'Studio handed me the output it had already printed (46 chars) ...'. "
               "timeout_sentence_reached_model must be true on BOTH sides, or the call "
               "did not time out and nothing was measured; terminal_offered_to_model, "
               "approvals_clicked and commands_requested must also match.",
        verified="Ran 2026-09-09 against merge base fcaad20ef (:8990) and head 70be91e64 "
                 "(:9013), two isolated installs each stamped with its own SHA, the same "
                 "stand-in connection and the same `echo progress; sleep 300` under a "
                 "seeded one-minute Max Tool Call Duration (both turns took ~60s: 60.81s "
                 "BEFORE, 60.12s AFTER). Every predeclared key moved and no other did: "
                 "partial_output_reached_model false -> true, tool_message_len 37 -> 47, "
                 "tool_message_head 'Execution timed out after 60 seconds.' -> 'progress "
                 "Execution timed out after 60 seconds.', ui_answer_says_output_kept "
                 "false -> true, ui_answer_says_output_lost true -> false. The composite "
                 "shows one sentence differing: BEFORE 'Studio handed me nothing it "
                 "printed (37 chars), and it did say the call timed out.' vs AFTER "
                 "'Studio handed me the output it had already printed (47 chars), and it "
                 "did say the call timed out.' Controls held on BOTH sides: "
                 "terminal_offered_to_model true, provider_completions 2, "
                 "commands_requested ['echo progress; sleep 300'], "
                 "seeded_tool_call_timeout_minutes '1', tool_cards_rendered 1, "
                 "timeout_sentence_reached_model true. "
                 "TWO corrections to the prediction, both worth keeping: "
                 "card_shows_printed_output is true on BOTH sides, not false -> true -- "
                 "the tool CARD renders the LIVE streamed output, so the user always saw "
                 "`progress`; only the model did not, which is exactly the gap this PR "
                 "closes and is why the assistant's own sentence, not the card, is the "
                 "measurement. And approvals_clicked is 0 on both sides, not 1: the "
                 "composer's permission mode is 'Approve for me', so the terminal call "
                 "auto-approved. Both are held identical across the pair.",
    ),
    10663: ScenePlan(
        pr=10663, scene="research_no_evidence",
        what="the Deep Research assistant message after a run whose ONE research step "
             "gathered nothing -- web_search is proxied to a dead port on both sides, so "
             "the step fails identically -- driven end to end through the real backend "
             "against a saved OpenAI-compatible connection served from the scene process, "
             "so no weights, GPU, download or real provider is involved",
        expect="BEFORE (merge base) nothing notices the evidence never arrived: the run is "
               "marked completed and the model's memory-written report is delivered with "
               "zero sources (run_status completed, report_delivered true, "
               "memory_report_delivered true, sources_count 0, synthesis_ran true). "
               "AFTER (head) the run fails with the search error before synthesis and no "
               "report is shown (run_status completed -> failed, report_delivered true -> "
               "false, memory_report_delivered true -> false, assistant_shows_report true "
               "-> false, error_names_no_evidence false -> true, synthesis_ran true -> "
               "false). sources_count must be 0 and steps_failed 1 on BOTH sides, or the "
               "step did not fail identically and nothing was measured.",
        verified="confirmed on base fcaad20ef vs head 2b5218b6e, two isolated installs on "
                 ":9800/:9801 with PR_UI_PORT_BASE=9800, web_search proxied to a dead port "
                 "(HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:9, NO_PROXY=127.0.0.1,localhost) "
                 "on BOTH sides. Every predicted key moved and no other did: run_status "
                 "completed -> failed, report_delivered true -> false, report_chars 389 -> "
                 "0, memory_report_delivered true -> false, assistant_shows_report true -> "
                 "false, assistant_research_status completed -> failed, "
                 "error_names_no_evidence false -> true, synthesis_ran true -> false, "
                 "model_phases [planning, decision, synthesis_audit, synthesis] -> "
                 "[planning, decision], and AFTER's report_sha e3b0c44298fc1c14 is the "
                 "SHA-256 of the empty string. The controls held on BOTH sides -- "
                 "steps_recorded 1, steps_completed 0, steps_failed 1, sources_count 0, "
                 "document_sources_count 0 -- so the two Studios did the identical work and "
                 "failed the identical step, and only the outcome differs. The composite "
                 "reads plainly: BEFORE a green 'Deep research completed - 0 sources' above "
                 "an invented report on a March 2026 release ('streaming writer', "
                 "'Q4_K_XL', 'shape validation'); AFTER a red 'Research could not be "
                 "completed' card naming the connection refused. NOTE: synthesis is never "
                 "reached AFTER, which is the cheapest proof the guard fires before the "
                 "model is asked to write anything.",
    ),

    10662: ScenePlan(
        pr=10662, scene="literal_think_tags_visible",
        what="the assistant bubble under a composer whose Thinking toggle has been clicked "
             "off, on a resident Qwen3-0.6B GGUF loaded with a chat_template_override that "
             "is a real hybrid enable_thinking template, so Studio's own detection publishes "
             "the enable_thinking style and the composer offers the plain Thinking pill on "
             "BOTH sides. The template's system line pins the answer to one exact sentence, "
             "'Use <think>hi</think> in your prompt.', which is the shape a user gets when "
             "they ask a model how to write a think tag. The pill is clicked off in the real "
             "composer and the turn is sent from the real composer, so the request carries "
             "the composer's own thinking: {\"type\": \"disabled\"}. The same turn is also "
             "put straight at /v1/chat/completions with enable_thinking false and at "
             "/v1/responses with reasoning.effort 'none', for the numbers.",
        expect="BEFORE (merge base fcaad20ef) the reply is still run through the typed-"
               "thinking splitter even though the request turned thinking off, so the tags "
               "are cut out of the answer: the bubble reads 'Use  in your prompt.' with the "
               "middle missing, ui_shows_think_tags False, api_shows_think_tags False, "
               "api_reasoning_content 'hi', and responses_output_kinds ['reasoning', "
               "'message'] with responses_shows_think_tags False. AFTER (head) the gate "
               "reads the request, the splitter does not run, and the sentence stays whole: "
               "ui_shows_think_tags False -> True with the bubble reading the full 'Use "
               "<think>hi</think> in your prompt.', api_shows_think_tags False -> True, "
               "api_reasoning_content 'hi' -> '', and responses_output_kinds ['reasoning', "
               "'message'] -> ['message'] with responses_shows_think_tags False -> True. "
               "Controls that must NOT move: reasoning_style stays 'enable_thinking', "
               "thinking_pill_active stays 'false', every http status stays 200, and the "
               "thinking-ON control stays split on BOTH sides "
               "(control_thinking_on_reasoning 'hi', control_thinking_on_content 'Use  in "
               "your prompt.') -- that is the proof the model really typed the tags and that "
               "both installs can still parse them. If the pill reads 'true', a status is not "
               "200, or the control pair moves, the turn never ran with thinking off or "
               "parsing broke, and the pair proves nothing.",
        kwargs={
            "model_path": "/Users/nilay/.cache/huggingface/hub/models--unsloth--gemma-3-270m-it-GGUF/"
                          "snapshots/c90975dbd40c0c7b275fefaae758c3415c906238/"
                          "gemma-3-270m-it-Q4_K_M.gguf",
            "context_length": 2048,
        },
        needs_model=True,
        verified="Ran 2026-09-10 against merge base fcaad20ef (:9004) and head b59af700b "
                 "(:9005), two isolated installs, one resident gemma-3-270m-it-GGUF each, "
                 "same template, same prompt. Controls held on both sides: reasoning_style "
                 "'enable_thinking', thinking_pill_active 'false', all three http statuses "
                 "200, and the thinking-ON control identical "
                 "(control_thinking_on_reasoning 'hi', control_thinking_on_content 'Use  in "
                 "your prompt.') -- so the model really typed the tags and both installs can "
                 "still parse them. Every predeclared API fact moved and no other did: "
                 "api_content 'Use  in your prompt.' -> 'Use <think>hi</think> in your "
                 "prompt.', api_reasoning_content 'hi' -> '', api_shows_think_tags False -> "
                 "True, responses_output_kinds ['reasoning','message'] -> ['message'], "
                 "responses_shows_think_tags False -> True. "
                 "NOT as predicted on the UI: ui_shows_think_tags stayed False and the "
                 "bubble reads 'Use / Thought for 0 seconds / in your prompt.' on BOTH "
                 "sides. Cause found and confirmed in the source, not guessed: the frontend "
                 "runs its own splitter over the reply text, parseAssistantContent(raw) in "
                 "features/chat/utils/parse-assistant-content.ts:104, which takes no gate "
                 "argument and splits <think> unconditionally. So the server-side gate this "
                 "PR fixes cannot reach the chat bubble; the API surfaces are the honest "
                 "evidence for it, and the client-side splitter is a separate defect.",
    ),
    900009: ScenePlan(
        pr=900009, scene="rag_scope_on_hosted_turn",
        what="the POST body the built bundle sends to /v1/chat/completions for a chat "
             "whose model is an external connection and whose Docs pill is on, "
             "intercepted in the browser and answered with a canned stream",
        expect="BEFORE (upstream/main) the external branch fills rag_scope.context_length "
               "from `runtime.loadedContextLength ?? params.maxSeqLength`, with no "
               "external guard, so a request that never touches a local model still "
               "carries a local window: context_length_sent True and "
               "rag_scope_context_length is a number. AFTER the guard makes it undefined "
               "for an external request and JSON.stringify drops the key: "
               "context_length_sent True -> False, rag_scope_context_length <number> -> "
               "None. Controls that must NOT move: requests_captured stays 1, "
               "rag_scope_present stays True and rag_scope_keys keeps thread_id / "
               "default_top_k / mode / autoinject on both sides -- the scope itself is "
               "still sent, only the window is dropped.",
    ),
    10810: ScenePlan(
        pr=10810, scene="workspace_rerun_after_edit",
        what="the chat after a scripted OpenAI-compatible connection drives four real calls "
             "through the real Studio tool loop with Code on: edit_file creates notes.txt "
             "('version one'), terminal `cat notes.txt`, edit_file changes it to "
             "'version two', then the identical terminal `cat notes.txt` again. The "
             "stand-in's final sentence only quotes what the second cat handed back.",
        expect="BEFORE (fresh upstream main bf87e2917) the second `cat notes.txt` has the "
               "same (name, arguments) key as the first successful one, so the controller "
               "treats it as a duplicate no-op even though edit_file changed the file: "
               "rerun_after_edit_executed false, rerun_after_edit_skipped_as_duplicate true, "
               "and the chat reads '... the cat after the edit was NOT run.' AFTER (fresh "
               "main + PR head 56ccabc12) the edit forgets the earlier workspace keys: "
               "rerun_after_edit_executed true, rerun_after_edit_result_head contains "
               "'version two', and the chat reads '... printed version two.' Controls that "
               "must hold on BOTH sides: read_before_edit_printed_version_one true, "
               "code_pill_active 'true', terminal/edit_file offered to the model.",
        verified="Ran 2026-09-11 on GitHub Actions (NilayYadav/unsloth, ubuntu-latest, "
                 "chromium 1280x1400): BEFORE branch evidence/pr10810-202609112038-negative "
                 "run 34645585151, AFTER branch evidence/pr10810-202609112038-positive run "
                 "34645587086, identical workflow/harness/tests. BEFORE: tool results "
                 "['Created notes.txt (2 lines)', 'version one', 'Edited notes.txt (1 "
                 "replacement)'], tool_results_returned 3, rerun_after_edit_executed false, "
                 "card_3 output 'Unsloth did not run this call because an identical one had "
                 "already completed.', answer '... the cat after the edit was NOT run.' "
                 "AFTER: tool_results_returned 3 -> 4, fourth result 'version two', "
                 "rerun_after_edit_executed false -> true, answer '... printed version two.' "
                 "Controls held on both sides: read_before_edit_printed_version_one true, "
                 "code_pill_active 'true', approvals_clicked 2, tool_cards_rendered 4, "
                 "provider_completions 5. Note rerun_after_edit_skipped_as_duplicate is false "
                 "on BEFORE because the loop never replays the skipped call's message to the "
                 "provider; the skip is proven by the card text instead.",
    ),
    10809: ScenePlan(
        pr=10809, scene="training_recents_api_key_start",
        what="the owner's Train page (/studio) sidebar after a tokenless API key calls "
             "POST /api/train/start for the private model unsloth-probe/private-llama. Both "
             "Studios hold the same saved Hugging Face login, have the model cached, and point "
             "HF_ENDPOINT at the same stand-in Hub that only serves the private repo to the "
             "saved login or a caller token.",
        expect="BEFORE (merge base 8ee07d6ae) the API-key start is accepted "
               "(api_key_start_http_status 200, api_key_start_result queued), a run for "
               "unsloth-probe/private-llama is recorded (runs_for_private_model >= 1), the "
               "sidebar shows the Recents group with that row (ui_recents_group_visible true, "
               "ui_private_model_rows >= 1), and server_login_requests >= 1. AFTER (PR head "
               "a242187445) the start is refused (api_key_start_http_status 422, "
               "api_key_start_result hf_model_access_denied), runs_for_private_model 0, no "
               "Recents group and no row (ui_recents_group_visible false, "
               "ui_private_model_rows 0), and server_login_requests 0. Controls that must hold "
               "on BOTH sides: same stand-in Hub, same saved login, same cached snapshot, same "
               "request body, and the photographed page is /studio.",
        kwargs={"model_name": "unsloth-probe/private-llama"},
        verified="Ran 2026-09-12 on Modal (NVIDIA L4, CUDA torch 2.6.0, chromium 1440x900), "
                 "base 8ee07d6ae vs head a242187445, separate frontend builds and homes. "
                 "backend_chat_only false and ui_url /studio on BOTH sides. BEFORE: "
                 "api_key_start_http_status 200 queued, runs_for_private_model 1 (status error), "
                 "server_login_requests 6, Recents group visible with the "
                 "unsloth-probe/private-llama row (ui_private_model_rows 1). AFTER: 422 "
                 "hf_model_access_denied, runs_for_private_model 0, server_login_requests 0, no "
                 "Recents group (ui_private_model_rows 0), Current Run tab disabled. A first CPU "
                 "attempt was discarded: chat_only redirected /studio to /chat on both sides and "
                 "only the random greeting differed.",
    ),
    10944: ScenePlan(
        pr=10944, scene="vision_history_image_turn", needs_model=True,
        what="a transformers vision chat (Linux, CUDA): turn 1 gives the code word "
             "PELICAN-42, turn 2 attaches an image reading CAT, turn 3 asks for the code word",
        expect="The frontend sends the same full thread on BOTH sides (ui_turn3_request roles "
               "and image_parts_per_message identical, mentions_code_word true). BEFORE (merge "
               "base 03af220ac0) the backend collapses the image thread to the newest question "
               "plus the image, so turn 3 cannot name the code word "
               "(ui_turn3_recalls_code_word false, api_image_chat_recall_has_code_word false, "
               "small api_image_chat_recall.prompt_tokens). AFTER (head fab6233c44) the whole "
               "thread reaches the model: both flags true and prompt_tokens larger. Controls on "
               "BOTH sides: the text-only thread recalls the code word and the image is still "
               "read on a later turn (api_image_chat_reads_image_has_word true).",
        kwargs={"model": "unsloth/Qwen2-VL-2B-Instruct-bnb-4bit"},
    ),

}


def plan_for(pr: int) -> ScenePlan:
    if pr not in REGISTRY:
        raise KeyError(
            f"no scene registered for PR {pr}. Add a ScenePlan to REGISTRY with an "
            f"`expect` describing the difference the screenshot must show."
        )
    return REGISTRY[pr]
