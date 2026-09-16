import os, signal, subprocess, sys, tempfile, textwrap, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
work = Path(tempfile.mkdtemp())
cfg = work / "cfg.yaml"
cfg.write_text("model: unsloth/Qwen3-0.6B\ndata:\n  dataset: yahma/alpaca-cleaned\n")
marker = work / "started"
ckpt = work / "checkpoint-saved"

driver = work / "driver.py"
driver.write_text(textwrap.dedent(f"""
    import queue, sys, time
    sys.path.insert(0, {str(ROOT)!r})
    sys.path.insert(0, {str(ROOT / 'studio' / 'backend')!r})
    import unsloth_cli.commands.train as t
    from unsloth_cli import app
    mode, steps = sys.argv[1], int(sys.argv[2])

    def worker(config, event_queue, stop_queue):
        open({str(marker)!r}, "w").close()
        for step in range(1, steps + 1):
            event_queue.put({{"type": "progress", "step": step, "total_steps": steps}})
            try:
                stop_queue.get(timeout = 0.5)
                open({str(ckpt)!r}, "w").write(str(step))
                event_queue.put({{"type": "complete", "output_dir": "out", "status_message": "Training stopped"}})
                return
            except queue.Empty:
                pass
        event_queue.put({{"type": "complete", "output_dir": "out", "status_message": "Training completed"}})

    def make():
        from core.training.training import create_mlx_trainer_adapter
        a = create_mlx_trainer_adapter()
        a.load_model = lambda **k: True
        a.prepare_model_for_training = lambda **k: True
        a.load_and_format_dataset = lambda **k: ([], None)
        a._model_config = {{"model_name": "unsloth/Qwen3-0.6B"}}
        a._dataset_config = {{"dataset_source": "yahma/alpaca-cleaned"}}
        a._build_worker_config = lambda training_args: {{}}
        a._run_mlx_worker = worker
        return a

    t._create_cli_trainer = lambda *args: make()
    app(prog_name = "unsloth", args = ["train", "-c", {str(cfg)!r}])
"""))

def run(label, steps, interrupt):
    for p in (marker, ckpt):
        p.unlink(missing_ok = True)
    cmd = f'"{sys.executable}" "{driver}" mlx {steps}; rc=$?; echo "UNSLOTH_TRAIN_RC=$rc"; [ $rc -eq 0 ] && echo EXPORT_RAN'
    # Mirror a shell chain `unsloth train ... && unsloth export`; rc is echoed first so it is visible either way.
    proc = subprocess.Popen(["bash", "-c", cmd], stdout = subprocess.PIPE, stderr = subprocess.STDOUT, text = True, start_new_session = True)
    if interrupt:
        deadline = time.time() + 120
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.1)
        time.sleep(1.5)
        os.killpg(proc.pid, signal.SIGINT)
    out, _ = proc.communicate(timeout = 300)
    rc = next((l.split("=")[1] for l in out.splitlines() if l.startswith("UNSLOTH_TRAIN_RC=")), "?")
    export_ran = "EXPORT_RAN" in out
    saved = ckpt.read_text() if ckpt.exists() else "no"
    print(f"--- {label} ---\n{out}")
    print(f"RESULT {label}: exit_code={rc} checkpoint_saved_at_step={saved} chained_export_ran={export_ran}")
    return rc, export_ran

rc_c, ran_c = run("ctrl_c", 200, True)
rc_n, ran_n = run("natural_3_steps", 3, False)
ok = rc_c == "130" and not ran_c and rc_n == "0" and ran_n
print("PASS" if ok else "FAIL", f"ctrl_c_exit={rc_c} ctrl_c_chain_ran={ran_c} natural_exit={rc_n} natural_chain_ran={ran_n}")
sys.exit(0 if ok else 1)
