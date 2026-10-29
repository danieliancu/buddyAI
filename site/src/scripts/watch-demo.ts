/* The list-next-to-the-watch demos (components/WatchDemo.astro: "Notes & reminders, by voice", "What needs
 * Wi-Fi"): choosing an item shows its screen on the watch. An item with a spoken sentence (data-said) plays it
 * first as the user's chat bubble, then the result; others fade straight to their screen. Goes through the
 * items by itself while the section is in view, until the visitor uses the list. Reduced motion: no
 * auto-advance and no animation. */

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
/** How long the spoken sentence shows (listening, then thinking) before the result. */
const LISTEN_MS = 700;
const THINK_MS = 900;
/** How long each result stays before the next item (auto-advance only). */
const RESULT_MS = 3800;

function setup(root: HTMLElement) {
  const glass = root.querySelector<HTMLElement>(".ws-glass")!;
  const screen = root.querySelector<HTMLElement>("[data-screen]")!;
  const watch = root.querySelector<HTMLElement>("[data-vx-watch]")!;
  const said = root.querySelector<HTMLElement>("[data-vx-said]");
  const views = [...root.querySelectorAll<HTMLElement>(".ws-view")];
  const btns = [...root.querySelectorAll<HTMLButtonElement>("[data-vx]")];

  // scale the 410 x 502 screen to the glass in the photo (as in how-sim.ts)
  const fit = () => {
    const k = Math.min(glass.clientHeight / 502, glass.clientWidth / 410);
    if (k > 0) screen.style.setProperty("--k", String(k));
  };
  new ResizeObserver(fit).observe(glass);
  fit();

  let cur = 0;
  let token = 0;
  let auto = !reduced;
  let inView = false;
  let step: number | undefined;
  let next: number | undefined;

  function show(name: string, anim: "" | "slide-in" | "vx-fade") {
    for (const v of views) {
      const on = v.dataset.view === name;
      v.classList.remove("slide-in", "vx-fade");
      v.classList.toggle("is-on", on);
      if (on && anim) {
        void v.offsetWidth; // restart the animation
        v.classList.add(anim);
      }
    }
  }

  function schedule() {
    clearTimeout(next);
    if (auto && inView && !document.hidden) next = window.setTimeout(() => select((cur + 1) % btns.length, true), RESULT_MS);
  }

  function select(i: number, animate: boolean) {
    cur = i;
    const t = ++token;
    clearTimeout(step);
    clearTimeout(next);
    btns.forEach((b, j) => {
      b.setAttribute("aria-pressed", String(j === i));
      b.classList.toggle("is-active", j === i);
    });
    const b = btns[i];
    watch.setAttribute("aria-label", `On the watch: ${b.dataset.alt ?? ""}`);
    const isAsk = !!root.querySelector(`[data-view="r${i}"] .vx-listen`);
    const result = (anim: "" | "slide-in" | "vx-fade") => {
      if (t !== token) return;
      screen.dataset.conv = isAsk ? "listening" : "idle";
      show(`r${i}`, anim);
      schedule();
    };
    if (!animate || reduced) return result("");
    if (!said || !b.dataset.said) return result("vx-fade");
    // the user's sentence as a chat bubble: listening, then thinking, then the result
    said.textContent = b.dataset.said;
    said.classList.remove("vx-pop");
    void said.offsetWidth;
    said.classList.add("vx-pop");
    screen.dataset.conv = "listening";
    show("chat", false);
    step = window.setTimeout(() => {
      if (t !== token) return;
      screen.dataset.conv = "thinking";
      step = window.setTimeout(() => result(isAsk ? "" : "slide-in"), THINK_MS);
    }, LISTEN_MS);
  }

  /** The visitor took over: no more auto-advance. */
  const stopAuto = () => {
    auto = false;
    clearTimeout(next);
  };

  let hover: number | undefined;
  btns.forEach((b, i) => {
    b.addEventListener("click", () => {
      stopAuto();
      clearTimeout(hover);
      select(i, true);
    });
    b.addEventListener("mouseenter", () => {
      stopAuto();
      clearTimeout(hover);
      if (i !== cur) hover = window.setTimeout(() => select(i, true), 150);
    });
    b.addEventListener("mouseleave", () => clearTimeout(hover));
    // keyboard: moving through the list shows each item (a click also focuses, so only :focus-visible)
    b.addEventListener("focus", () => {
      stopAuto();
      if (i !== cur && b.matches(":focus-visible")) select(i, true);
    });
  });

  if (auto) {
    new IntersectionObserver(
      ([e]) => {
        inView = e.isIntersecting;
        if (inView) schedule();
        else clearTimeout(next);
      },
      { threshold: 0.4 },
    ).observe(watch);
    document.addEventListener("visibilitychange", () => (document.hidden ? clearTimeout(next) : schedule()));
  }
}

document.querySelectorAll<HTMLElement>("[data-vx-root]").forEach(setup);

export {};
