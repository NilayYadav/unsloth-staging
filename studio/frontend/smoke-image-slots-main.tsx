import { ImageDropzone, useGalleryImageDropSource } from "@/components/image-dropzone";
import { useStripReorder } from "@/hooks/use-strip-reorder";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./src/index.css";

const LOG = "http://127.0.0.1:8765/log";
const log = (event: string, data: unknown = {}) =>
  void fetch(LOG, { method: "POST", mode: "no-cors", body: JSON.stringify({ event, ...((data as object) ?? {}) }) }).catch(() => {});

function swatch(color: string): string {
  const c = document.createElement("canvas");
  c.width = 64;
  c.height = 64;
  const g = c.getContext("2d")!;
  g.fillStyle = color;
  g.fillRect(0, 0, 64, 64);
  return c.toDataURL("image/png");
}

const RED = swatch("#d00000");
const BLUE = swatch("#0050d0");
const toBlob = async () => (await fetch(BLUE)).blob();

function App() {
  const [a, setA] = useState<string | null>(RED);
  const [b, setB] = useState<string | null>(null);
  const [c, setC] = useState<string | null>(null);
  useGalleryImageDropSource(toBlob);
  const { tileProps } = useStripReorder(() => {});
  useEffect(() => {
    const report = () => {
      const rect = (id: string) => {
        const el = document.querySelector(`[data-slot="${id}"]`)!;
        const r = (id === "g" ? el : el.firstElementChild!).getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      };
      log("layout", { a: rect("a"), b: rect("b"), c: rect("c"), g: rect("g"), dpr: window.devicePixelRatio, iw: innerWidth, ih: innerHeight });
    };
    setTimeout(report, 500);
    const seen = new Set<string>();
    const mo = new MutationObserver(() => {
      for (const t of document.querySelectorAll("[data-sonner-toast]")) {
        const text = (t as HTMLElement).innerText.replace(/\s+/g, " ").trim();
        if (text && !seen.has(text)) {
          seen.add(text);
          log("toast", { text });
        }
      }
    });
    mo.observe(document.body, { childList: true, subtree: true });
    const w = (window as any).__TAURI__?.window?.getCurrentWindow?.();
    w?.show?.();
    w?.setFocus?.();
    for (const type of ["mousedown", "dragstart", "dragenter", "drop", "dragend"]) {
      document.addEventListener(type, (e) => {
        const t = e.target as HTMLElement;
        const dt = (e as DragEvent).dataTransfer;
        log(type, { target: t?.closest?.("[data-slot]")?.getAttribute("data-slot") ?? t?.tagName, types: dt ? [...dt.types] : undefined });
      }, true);
    }
    window.addEventListener("focus", () => log("focus"));
    const overSeen = new Set<string>();
    for (const type of ["dragover", "drop"]) {
      document.addEventListener(type, (e) => {
        const t = e.target as HTMLElement;
        const slot = t?.closest?.("[data-slot]")?.getAttribute("data-slot") ?? t?.tagName;
        if (type === "dragover") {
          if (overSeen.has(slot)) return;
          overSeen.add(slot);
        }
        log(type + "-bubble", { target: slot, prevented: e.defaultPrevented, effect: (e as DragEvent).dataTransfer?.dropEffect });
      });
    }
    document.addEventListener("dragstart", () => overSeen.clear(), true);
    (window as any).__TAURI__?.webview?.getCurrentWebview?.().onDragDropEvent?.(({ payload }: any) => {
      if (payload.type !== "over") log("native-" + payload.type, { paths: (payload.paths ?? []).map((p: string) => p.slice(0, 80)) });
    });
    log("ready", { tauri: Boolean((window as any).__TAURI_INTERNALS__) });
  }, []);
  return (
    <TooltipProvider>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24, padding: "80px 40px" }}>
        <div data-slot="a">
          <ImageDropzone value={a} onChange={(v) => { setA(v); log("change", { slot: "a", same: v === RED, blue: v !== null && v !== RED, empty: v === null }); }} />
        </div>
        <div data-slot="b">
          <ImageDropzone value={b} onChange={(v) => { setB(v); log("change", { slot: "b", same: v === RED, blue: v !== null && v !== RED, empty: v === null }); }} />
        </div>
      </div>
      <div data-slot="c" style={{ margin: "0 40px 24px", width: 328 }}>
        <ImageDropzone value={c} onChange={(v) => { setC(v); log("change", { slot: "c", same: v === RED, blue: v !== null && v !== RED, empty: v === null }); }} />
      </div>
      <div data-slot="g" {...tileProps("blue")} style={{ margin: "0 40px 24px", width: 96, height: 96, background: "#0050d0" }} />
      <div style={{ padding: "0 40px", fontSize: 14 }} id="state">
        slot b: {b === null ? "empty" : b === RED ? "copied from a" : "other image"}
      </div>
      <Toaster />
    </TooltipProvider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
