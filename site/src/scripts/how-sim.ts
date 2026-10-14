/* The "How it works" watch simulator: drives the HTML copy of the watch UI (components/how/WatchScreen.astro)
 * from the tabs and steps (components/how/tabs.ts), and lets people use it directly: tap the screen, swipe,
 * press the side buttons. Everything is scripted; nothing is sent anywhere. */

type View =
  | "watchface" | "chat" | "notes" | "note" | "reminders" | "reminder" | "settings"
  | "wifi" | "pairing" | "message" | "standby" | "off";
type Conv = "idle" | "listening" | "thinking" | "speaking";
type Hot = "talk" | "power";

interface Note { n: number; title: string; lines: string[]; pinned?: boolean }
interface Rem {
  n: number; day: number; time: string; end?: string; text: string; done?: boolean;
  location?: string; participants?: string; notify?: number;
}

const FA = {
  mic: "", speaker: "", right: "", pin: "", file: "", cal: "", bell: "",
  place: "", users: "", trash: "", check: "", warn: "",
};
const ROW_COLOURS = ["var(--a)", "#FF8A3D", "#34C759", "#AF52DE", "#FF4F81", "#32D2F5"];
const TIMEOUTS = [10, 15, 30, 60, 120, 300];
const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const CANCEL = Symbol("cancel");

const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const fa = (glyph: string, cls = "") => el("span", `fa ${cls}`.trim(), glyph);
const dayDate = (offset: number) => new Date(Date.now() + offset * 86400000);
const longDate = (d: Date) => `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
const pad = (n: number) => String(n).padStart(2, "0");

function setup(root: HTMLElement) {
  const q = <T extends Element = HTMLElement>(s: string) => root.querySelector<T>(s)!;
  const qa = <T extends Element = HTMLElement>(s: string) => [...root.querySelectorAll<T>(s)];
  const ws = q("[data-ws]");
  const glass = q(".ws-glass");
  const screen = q("[data-screen]");

  // ---- state -------------------------------------------------------------------------------------------
  const fresh = () => ({
    view: "watchface" as View,
    power: "on" as "on" | "sleep" | "off",
    conv: "idle" as Conv,
    theme: "midnight" as "midnight" | "mono",
    brightness: 80,
    volume: 60,
    timeout: 1,
    aod: false,
    page: 0,
    lang: "en" as "en" | "ro",
    talkTurn: 0,
    notes: [
      { n: 1, title: "Packing for Lisbon", lines: ["Passport", "Phone charger", "Sunglasses"], pinned: true },
      { n: 2, title: "Ideas for Ana's birthday", lines: ["Pottery class", "Concert tickets", "Weekend in Brighton"] },
      { n: 3, title: "Books to read", lines: ["The Midnight Library", "Atomic Habits"] },
    ] as Note[],
    rems: [
      { n: 1, day: 0, time: "08:30", text: "Take your vitamins", done: true },
      { n: 2, day: 0, time: "14:00", end: "15:00", text: "Team meeting", location: "Office, 3rd floor", participants: "Mihai, Sofia", notify: 15 },
      { n: 3, day: 0, time: "18:30", text: "Pick up Sofia from school" },
    ] as Rem[],
    note: 1,
    rem: 2,
  });
  let s = fresh();

  // ---- cancellable scripts -----------------------------------------------------------------------------
  let token = 0;
  const cancel = () => {
    token++;
    clearHighlights();
  };
  const wait = (ms: number) => {
    const t = token;
    return new Promise<void>((res, rej) => setTimeout(() => (t === token ? res() : rej(CANCEL)), reduced ? Math.min(ms, 250) : ms));
  };
  const run = (fn: () => Promise<void>) => {
    cancel();
    return fn().catch((e) => {
      if (e !== CANCEL) throw e;
    });
  };

  // ---- scale the 410 x 502 screen to the glass ---------------------------------------------------------
  const fit = () => {
    const k = Math.min(glass.clientHeight / 502, glass.clientWidth / 410);
    screen.style.setProperty("--k", String(k));
  };
  new ResizeObserver(fit).observe(glass);
  fit();

  // ---- views -------------------------------------------------------------------------------------------
  const ORDER: View[] = ["watchface", "notes", "reminders", "settings", "chat", "note", "reminder"];
  function show(v: View, how: "auto" | "none" = "auto") {
    if (v === s.view) return;
    const back = how === "auto" && ORDER.indexOf(v) < ORDER.indexOf(s.view) && ORDER.includes(v);
    const animate = how === "auto" && !reduced && ORDER.includes(v) && ORDER.includes(s.view);
    s.view = v;
    qa("[data-view]").forEach((sec) => {
      const on = sec.dataset.view === v;
      sec.classList.toggle("is-on", on);
      sec.classList.remove("slide-in", "slide-back");
      if (on && animate) {
        void sec.offsetWidth;
        sec.classList.add(back ? "slide-back" : "slide-in");
      }
    });
    if (v === "notes") renderNotes();
    if (v === "reminders") renderRems();
    if (v === "note") renderNote();
    if (v === "reminder") renderRem();
  }

  function setConv(c: Conv) {
    s.conv = c;
    screen.dataset.conv = c;
    q("[data-hint]").textContent = c === "listening" ? "Listening…" : c === "thinking" ? "Thinking…" : "";
    q("[data-chat-icon]").textContent = c === "speaking" ? FA.speaker : FA.mic;
  }

  // ---- highlights (on the screen and on the side buttons) ----------------------------------------------
  function clearHighlights() {
    qa(".ws-hl").forEach((e) => e.classList.remove("ws-hl"));
  }
  function highlight(sel: string) {
    qa(sel).forEach((e) => e.classList.add("ws-hl"));
  }
  function hint(h?: Hot) {
    qa<HTMLElement>("[data-hot]").forEach((b) => b.classList.toggle("is-hint", b.dataset.hot === h));
  }
  async function press(h: Hot, ms = 220) {
    const b = q(`[data-hot="${h}"]`);
    b.classList.add("is-pressed");
    try {
      await wait(ms);
    } finally {
      b.classList.remove("is-pressed");
    }
  }

  // ---- clock -------------------------------------------------------------------------------------------
  function tick() {
    const d = new Date();
    q("[data-time]").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    q("[data-date]").textContent = longDate(d);
    const m = d.getMinutes(), h = (d.getHours() % 12) + m / 60;
    const hand = (sel: string, deg: number, len: number) => {
      const a = (deg * Math.PI) / 180;
      const line = q<SVGLineElement>(`[data-hand="${sel}"]`);
      line.setAttribute("x2", String(205 + len * Math.sin(a)));
      line.setAttribute("y2", String(251 - len * Math.cos(a)));
    };
    hand("h", h * 30, 95);
    hand("m", m * 6, 115);
  }
  tick();
  setInterval(tick, 10000);

  // ---- badges ------------------------------------------------------------------------------------------
  function badges() {
    const set = (k: string, n: number) => {
      const b = q(`[data-badge="${k}"]`);
      b.textContent = String(n);
      b.hidden = n === 0;
    };
    set("notes", s.notes.length);
    set("reminders", s.rems.filter((r) => !r.done).length);
  }

  // ---- conversation ------------------------------------------------------------------------------------
  const chat = q("[data-chat]");
  const dots = (side: "me" | "ola") => {
    const d = el("div", `ch-dots ${side}`);
    d.append(el("i"), el("i"), el("i"));
    chat.append(d);
    return d;
  };
  async function type(node: HTMLElement, text: string, msPerChar: number) {
    if (reduced) {
      node.textContent = text;
      return;
    }
    for (let i = 1; i <= text.length; i++) {
      node.textContent = text.slice(0, i);
      await wait(msPerChar);
    }
  }
  const trimChat = () => {
    while (chat.children.length > 8) chat.firstElementChild?.remove();
  };

  /** One turn: listening -> the user's words -> thinking -> olá's reply, typed as it is spoken. */
  async function turn(said: string, reply: string, opts: { big?: boolean; keepListening?: boolean } = {}) {
    if (s.view !== "chat") show("chat");
    chat.querySelectorAll(".ch-dots").forEach((d) => d.remove());
    setConv("listening");
    const d1 = dots("me");
    trimChat();
    await wait(900);
    d1.remove();
    const me = el("div", "ch-b me");
    chat.append(me);
    await type(me, said, 28);
    await wait(350);
    setConv("thinking");
    const d2 = dots("ola");
    await wait(1300);
    d2.remove();
    setConv("speaking");
    const b = el("div", opts.big ? "ch-b big" : "ch-b ola");
    chat.append(b);
    trimChat();
    await type(b, reply, opts.big ? 90 : 32);
    await wait(500);
    setConv(opts.keepListening ? "listening" : "idle");
  }

  const TALKS: [string, string, boolean?][] = [
    ["What's the weather in London tomorrow?", "Tomorrow in London: cloudy in the morning, sunny spells after lunch, up to 17°C with a light breeze."],
    ["What's 15% of 80?", "12", true],
    ["Tell me a fun fact", "Octopuses have three hearts, and two of them stop beating when they swim."],
  ];
  async function talk() {
    if (s.power === "off") return;
    s.power = "on";
    if (s.view !== "chat") {
      chat.replaceChildren();
      show("chat", s.view === "standby" || s.view === "off" ? "none" : "auto");
    }
    const [said, reply, big] = TALKS[s.talkTurn++ % TALKS.length];
    await turn(said, reply, { big });
  }

  // ---- notes -------------------------------------------------------------------------------------------
  function renderNotes() {
    const body = q("[data-list=notes]");
    body.replaceChildren();
    const sorted = [...s.notes].sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned) || a.n - b.n);
    sorted.forEach((n, i) => {
      const row = el("div", "ls-row");
      row.style.setProperty("--c", ROW_COLOURS[i % ROW_COLOURS.length]);
      row.dataset.act = `note:${n.n}`;
      row.setAttribute("role", "button");
      row.tabIndex = 0;
      const text = el("div", "ls-text");
      if (n.pinned) text.append(fa(FA.pin, "ls-pin"));
      text.append(n.title);
      text.append(el("span", "ls-meta", n.lines.join(", ")));
      row.append(el("div", "ls-badge", `#${n.n}`), el("div", "ls-bar"), text, fa(FA.right, "ls-chev"));
      body.append(row);
    });
  }
  const curNote = () => s.notes.find((n) => n.n === s.note) ?? s.notes[0];
  function renderNote(flash = -1, live?: { help?: boolean; said?: string; ask?: string; bad?: string }) {
    const n = curNote();
    q("[data-note-head]").textContent = `Note #${n.n}`;
    const card = el("div", "dt-card");
    const top = el("div", "dt-top");
    top.append(el("div", "dt-tile fa", FA.file), el("div", "dt-title", n.title));
    card.append(top, el("div", "dt-div"));
    n.lines.forEach((l, i) => {
      const line = el("div", `dt-line${i === flash ? " flash" : ""}`);
      line.append(el("b", "", `${i + 1}.`), el("span", "", l));
      card.append(line);
    });
    if (live) {
      const lv = el("div", `dt-live${live.bad ? " bad" : ""}`);
      if (live.bad) lv.textContent = live.bad;
      else if (live.said) lv.append(el("span", "said", live.said));
      else if (live.ask) lv.append(el("span", "", live.ask)), (lv.style.color = "var(--a)");
      else lv.textContent = 'Say a line to add it, or "delete 2", "move 3 to the top".';
      card.append(lv);
    }
    const body = q("[data-note-body]");
    body.replaceChildren(card);
    if (flash >= 0 || live) body.scrollTop = body.scrollHeight;
    const bar = q("[data-note-bar]");
    const del = el("div", "dt-btn", "");
    del.append(fa(FA.trash));
    del.dataset.act = "note-del";
    const mic = el("div", "dt-btn mic");
    mic.append(fa(FA.mic));
    mic.dataset.act = "note-mic";
    const pin = el("div", `dt-btn${n.pinned ? " pinned" : ""}`);
    pin.append(fa(FA.pin));
    pin.dataset.act = "note-pin";
    [del, mic, pin].forEach((b) => (b.setAttribute("role", "button"), (b.tabIndex = 0)));
    del.setAttribute("aria-label", "Delete note");
    mic.setAttribute("aria-label", "Edit by voice");
    pin.setAttribute("aria-label", "Pin note");
    bar.replaceChildren(del, mic, pin);
  }
  const noteMic = (on: boolean) => q("[data-note-bar] .mic").classList.toggle("rec", on);

  // ---- reminders ---------------------------------------------------------------------------------------
  const remOrder = (a: Rem, b: Rem) => a.day - b.day || Number(!!a.done) - Number(!!b.done) || a.time.localeCompare(b.time);
  function remRow(r: Rem, i: number) {
    const row = el("div", `ls-row${r.done ? " done" : ""}`);
    if (!r.done) row.style.setProperty("--c", ROW_COLOURS[i % ROW_COLOURS.length]);
    row.dataset.act = `rem:${r.n}`;
    row.setAttribute("role", "button");
    row.tabIndex = 0;
    const text = el("div", "ls-text", r.text);
    const meta = el("span", "ls-meta", r.end ? `${r.time} – ${r.end}` : r.time);
    if (r.notify) {
      const bell = fa(FA.bell);
      bell.style.color = "var(--a)";
      meta.append(" ", bell);
    }
    if (r.done) meta.append(" ", el("span", "ok", "✓ Completed"));
    text.append(meta);
    row.append(el("div", "ls-badge", `#${r.n}`), el("div", "ls-bar"), text, fa(FA.right, "ls-chev"));
    return row;
  }
  function renderRems() {
    const body = q("[data-list=reminders]");
    body.replaceChildren();
    const days = [...new Set(s.rems.map((r) => r.day))].sort((a, b) => a - b);
    let i = 0;
    for (const day of days) {
      const head = el("div", "ls-day");
      const d = dayDate(day);
      head.append(el("b", "", day === 0 ? "Today" : day === 1 ? "Tomorrow" : WEEKDAYS[d.getDay()]), el("span", "", `${d.getDate()} ${MONTHS[d.getMonth()]}`));
      const rows = s.rems.filter((r) => r.day === day).sort(remOrder).map((r) => remRow(r, i++));
      if (day === 0) {
        const frame = el("div", "ls-today");
        frame.append(head, ...rows);
        body.append(frame);
      } else body.append(head, ...rows);
    }
  }
  const curRem = () => s.rems.find((r) => r.n === s.rem) ?? s.rems[0];
  function renderRem(opts: { flash?: string; live?: string; said?: string; status?: string } = {}) {
    const r = curRem();
    q("[data-rem-head]").textContent = `Reminder #${r.n}`;
    const card = el("div", "dt-card rm-card");
    const top = el("div", "dt-top");
    const col = el("div");
    col.append(el("div", "rm-date", longDate(dayDate(r.day))));
    const time = el("div", "rm-time", r.end ? `${r.time} – ${r.end}` : r.time);
    if (opts.flash === "time") time.classList.add("dt-line", "flash");
    col.append(time);
    if (r.done) col.append(el("div", "ok", "✓ Completed"));
    else if (opts.status) col.append(el("div", "", opts.status)), ((col.lastChild as HTMLElement).style.color = "var(--a)");
    top.append(el("div", "dt-tile rm-tile fa", FA.cal), col);
    card.append(top);
    const info = (icon: string, label: string, value: string, key: string) => {
      const row = el("div", `rm-info${opts.flash === key ? " dt-line flash" : ""}`);
      const t = el("div");
      t.append(el("small", "", label), el("span", "", value));
      row.append(el("div", "ico fa", icon), t);
      card.append(row);
    };
    if (r.location) info(FA.place, "Location", r.location, "location");
    info(FA.file, "Description", r.text, "text");
    if (r.notify) info(FA.bell, "Reminder", `${r.notify} min before`, "notify");
    if (r.participants) info(FA.users, "Participants", r.participants, "participants");
    if (opts.live || opts.said) {
      const lv = el("div", "dt-live");
      if (opts.said) lv.append(el("span", "said", opts.said));
      else lv.textContent = opts.live!;
      card.append(lv);
    }
    const body = q("[data-rem-body]");
    body.replaceChildren(card);
    if (opts.flash || opts.live || opts.said) {
      const target = body.querySelector(".flash, .dt-live") as HTMLElement | null;
      if (target) body.scrollTop = target.offsetTop - 40;
    }
    const bar = q("[data-rem-bar]");
    const del = el("div", "dt-btn");
    del.append(fa(FA.trash));
    const mic = el("div", "dt-btn mic");
    mic.append(fa(FA.mic));
    mic.dataset.act = "rem-mic";
    const chk = el("div", `dt-btn${r.done ? " checked" : ""}`);
    chk.append(fa(FA.check));
    chk.dataset.act = "rem-check";
    del.dataset.act = "rem-del";
    [del, mic, chk].forEach((b) => (b.setAttribute("role", "button"), (b.tabIndex = 0)));
    del.setAttribute("aria-label", "Delete reminder");
    mic.setAttribute("aria-label", "Change by voice");
    chk.setAttribute("aria-label", r.done ? "Reopen" : "Complete");
    bar.replaceChildren(del, mic, chk);
  }
  const remMic = (on: boolean) => q("[data-rem-bar] .mic").classList.toggle("rec", on);

  // ---- settings ----------------------------------------------------------------------------------------
  function applySettings() {
    screen.dataset.theme = s.theme;
    const wave = q<HTMLImageElement>("[data-wave]");
    wave.src = (s.theme === "mono" ? wave.dataset.white : wave.dataset.blue)!;
    const th = q("[data-act=theme]");
    th.classList.toggle("on", s.theme === "mono");
    th.setAttribute("aria-checked", String(s.theme === "mono"));
    const level = (k: "brightness" | "volume") => {
      const card = q(`[data-level=${k}]`);
      const v = s[k];
      card.querySelector("[data-val]")!.textContent = `${v}%`;
      const on = Math.ceil(v / 10);
      card.querySelectorAll("i").forEach((b, i) => b.classList.toggle("on", i < on));
      card.querySelector("[role=slider]")!.setAttribute("aria-valuenow", String(v));
    };
    level("brightness");
    level("volume");
    screen.style.filter = `brightness(${0.45 + (0.55 * s.brightness) / 100})`;
    const t = TIMEOUTS[s.timeout];
    q("[data-timeout]").textContent = t >= 60 ? `${t / 60} min` : `${t} s`;
    const aod = q("[data-act=aod]");
    aod.classList.toggle("on", s.aod);
    aod.setAttribute("aria-checked", String(s.aod));
    q("[data-act='lang:en']").classList.toggle("on", s.lang === "en");
    const other = q("[data-lang-other]");
    other.classList.toggle("on", s.lang === "ro");
    other.textContent = s.lang === "ro" ? "Română" : "Other";
    qa("[data-page]").forEach((p) => p.classList.toggle("is-on", p.dataset.page === String(s.page)));
    qa("[data-pdot]").forEach((d, i) => d.classList.toggle("on", i === s.page));
    q("[data-act='page:-1']").classList.toggle("off", s.page === 0);
    q("[data-act='page:1']").classList.toggle("off", s.page === 1);
  }
  function setPage(p: number) {
    s.page = Math.max(0, Math.min(1, p));
    applySettings();
  }
  async function animateLevel(k: "brightness" | "volume", to: number) {
    const step = to > s[k] ? 10 : -10;
    while (s[k] !== to) {
      s[k] = Math.abs(to - s[k]) < 10 ? to : s[k] + step;
      applySettings();
      await wait(110);
    }
  }

  // ---- power -------------------------------------------------------------------------------------------
  function sleep() {
    s.power = "sleep";
    setConv("idle");
    show(s.aod ? "standby" : "off", "none");
  }
  function wake() {
    s.power = "on";
    show("watchface", "none");
  }
  async function powerOff() {
    q("[data-msg]").textContent = "Turning off…";
    show("message", "none");
    await wait(1500);
    s.power = "off";
    show("off", "none");
  }
  async function powerOn(to: View = "watchface") {
    await wait(700);
    s.power = "on";
    show(to, "none");
  }

  /** The network name line: shown on the simulated Wi-Fi setup screen only for the iPhone step. */
  const iphoneLine = (on: boolean) => (q("[data-wi-join]").hidden = !on);

  // ---- step actions (tabs.ts `run`) ---------------------------------------------------------------------
  const ensureOn = () => {
    if (s.power !== "on" || s.view === "message") {
      s.power = "on";
      show("watchface", "none");
    }
  };
  const home = () => {
    ensureOn();
    setConv("idle");
    show("watchface");
  };
  const openSettings = async (page = 0) => {
    ensureOn();
    s.page = page;
    applySettings();
    show("settings");
  };
  const ACTIONS: Record<string, () => Promise<void>> = {
    home: async () => home(),
    "hl:mic": async () => {
      home();
      highlight(".wf-mic");
    },
    "hl:shortcuts": async () => {
      home();
      highlight(".wf-short");
    },
    standby: async () => {
      home();
      await wait(800);
      await press("power");
      const aod = s.aod;
      s.aod = true;
      sleep();
      s.aod = aod;
      await wait(2600);
      await press("power");
      wake();
    },
    poweroff: async () => {
      home();
      await wait(700);
      await press("power", 2000);
      await powerOff();
      await wait(1500);
      await press("power", 1000);
      await powerOn();
    },
    off: async () => {
      setConv("idle");
      s.power = "off";
      show("off", "none");
    },
    listen: async () => {
      ensureOn();
      home();
      await wait(600);
      await press("talk");
      chat.replaceChildren();
      show("chat");
      setConv("listening");
      dots("me");
    },
    "chat:weather": async () => {
      ensureOn();
      chat.replaceChildren();
      await turn(TALKS[0][0], TALKS[0][1]);
    },
    "chat:math": async () => {
      ensureOn();
      await turn("What's 15% of 80?", "12", { big: true });
    },
    "chat:followup": async () => {
      ensureOn();
      await turn("Remind me to buy flowers", "Sure. What time?", { keepListening: true });
      await wait(700);
      await turn("At six", "Done: today at 18:00, buy flowers.");
      if (!s.rems.some((r) => r.text === "Buy flowers")) s.rems.push({ n: 5, day: 0, time: "18:00", text: "Buy flowers" });
      badges();
    },
    "chat:end": async () => {
      ensureOn();
      if (s.view !== "chat") {
        chat.replaceChildren();
        await turn(TALKS[2][0], TALKS[2][1]);
      }
      highlight("[data-view=chat] .ws-x");
      await wait(1800);
      clearHighlights();
      home();
    },
    "notes:dictate": async () => {
      ensureOn();
      chat.replaceChildren();
      const exists = s.notes.some((n) => n.n === 4);
      await turn("Note: shopping list, milk, eggs and coffee", "Saved as note 4: Shopping list, with 3 lines.");
      if (!exists) s.notes.push({ n: 4, title: "Shopping list", lines: ["Milk", "Eggs", "Coffee"] });
      badges();
      s.note = 4;
      await wait(900);
      show("note");
    },
    "notes:list": async () => {
      ensureOn();
      if (s.view === "watchface") highlight(".wf-short.s1");
      else home();
      await wait(700);
      clearHighlights();
      show("notes");
    },
    "notes:open": async () => {
      ensureOn();
      if (s.view !== "notes") show("notes");
      s.note = s.notes.some((n) => n.n === 4) ? 4 : 2;
      await wait(500);
      highlight(`[data-act="note:${s.note}"]`);
      await wait(800);
      show("note");
    },
    "notes:edit": async () => {
      ensureOn();
      if (!s.notes.some((n) => n.n === 4)) s.notes.push({ n: 4, title: "Shopping list", lines: ["Milk", "Eggs", "Coffee"] });
      s.note = 4;
      const n = curNote();
      n.lines = ["Milk", "Eggs", "Coffee"];
      show("note");
      renderNote();
      await wait(700);
      noteMic(true);
      renderNote(-1, { help: true });
      noteMic(true);
      await wait(1800);
      renderNote(-1, { said: "Add bread" });
      noteMic(true);
      await wait(1100);
      n.lines.push("Bread");
      renderNote(n.lines.length - 1, { help: true });
      noteMic(true);
      await wait(1800);
      renderNote(-1, { said: "Delete 2" });
      noteMic(true);
      await wait(1000);
      renderNote(1, { ask: "Delete line 2, Eggs?" });
      noteMic(true);
      await wait(1600);
      renderNote(1, { said: "Yes" });
      noteMic(true);
      await wait(900);
      n.lines.splice(1, 1);
      renderNote(-1, { help: true });
      noteMic(true);
      await wait(1400);
      renderNote();
    },
    "notes:pin": async () => {
      ensureOn();
      if (s.view !== "note") show("note");
      const n = curNote();
      highlight("[data-note-bar] [data-act=note-pin]");
      await wait(900);
      n.pinned = !n.pinned;
      renderNote();
      await wait(1200);
      highlight("[data-note-bar] [data-act=note-del]");
      await wait(700);
      armDelete();
      await wait(3000);
      renderNote();
    },
    "rem:dictate": async () => {
      ensureOn();
      chat.replaceChildren();
      await turn("Remind me tomorrow at 9 to take the car to the garage", "Done: tomorrow at 09:00, take the car to the garage.");
      if (!s.rems.some((r) => r.n === 4)) s.rems.push({ n: 4, day: 1, time: "09:00", text: "Take the car to the garage" });
      badges();
      await wait(800);
      show("reminders");
    },
    "rem:list": async () => {
      ensureOn();
      if (s.view === "watchface") highlight(".wf-short.s2");
      else home();
      await wait(700);
      clearHighlights();
      show("reminders");
    },
    "rem:open": async () => {
      ensureOn();
      if (s.view !== "reminders") show("reminders");
      s.rem = 2;
      await wait(500);
      highlight('[data-act="rem:2"]');
      await wait(800);
      show("reminder");
    },
    "rem:edit": async () => {
      ensureOn();
      s.rem = 2;
      const r = curRem();
      Object.assign(r, { time: "14:00", end: "15:00", participants: "Mihai, Sofia", notify: 15 });
      show("reminder");
      renderRem();
      await wait(700);
      renderRem({ live: 'Say a change: "at 10:30", "add Ana", "remove the place".' });
      remMic(true);
      await wait(1700);
      renderRem({ said: "At 15:30" });
      remMic(true);
      await wait(1000);
      Object.assign(r, { time: "15:30", end: "16:30" });
      renderRem({ flash: "time" });
      remMic(true);
      await wait(1500);
      renderRem({ said: "Add Ana" });
      remMic(true);
      await wait(1000);
      r.participants = "Mihai, Sofia, Ana";
      renderRem({ flash: "participants" });
      remMic(true);
      await wait(1500);
      renderRem({ said: "Remind me 30 minutes before" });
      remMic(true);
      await wait(1000);
      r.notify = 30;
      renderRem({ flash: "notify" });
      remMic(true);
      await wait(1500);
      renderRem();
    },
    "rem:done": async () => {
      ensureOn();
      s.rem = 3;
      curRem().done = false;
      show("reminder");
      renderRem();
      await wait(600);
      highlight("[data-rem-bar] [data-act=rem-check]");
      await wait(900);
      curRem().done = true;
      badges();
      renderRem();
      await wait(1400);
      show("reminders");
    },
    "rem:alert": async () => {
      ensureOn();
      home();
      await wait(600);
      const aod = s.aod;
      s.aod = true;       // the standby clock, as in "Wake and sleep"
      sleep();
      s.aod = aod;
      await wait(1800);
      s.power = "on";
      s.rem = 2;
      curRem().done = false;
      show("reminder", "none");
      renderRem({ status: `Starts in ${curRem().notify ?? 15} min` });
    },
    "set:open": async () => {
      home();
      highlight(".wf-short.s3");
      await wait(900);
      clearHighlights();
      await openSettings(0);
    },
    "set:theme": async () => {
      await openSettings(0);
      highlight("[data-act=theme]");
      await wait(900);
      s.theme = s.theme === "midnight" ? "mono" : "midnight";
      applySettings();
    },
    "set:levels": async () => {
      await openSettings(0);
      highlight("[data-level=brightness] .st-blocks");
      await animateLevel("brightness", 100);
      await wait(400);
      await animateLevel("brightness", 70);
      clearHighlights();
      highlight("[data-level=volume] .st-blocks");
      await animateLevel("volume", 30);
      await wait(400);
      await animateLevel("volume", 80);
    },
    "set:timeout": async () => {
      await openSettings(0);
      highlight("[data-act=timeout]");
      for (let i = 0; i < 2; i++) {
        await wait(900);
        s.timeout = (s.timeout + 1) % TIMEOUTS.length;
        applySettings();
      }
    },
    "set:page2": async () => {
      await openSettings(0);
      await wait(600);
      highlight("[data-act='page:1']");
      await wait(700);
      clearHighlights();
      setPage(1);
      await wait(700);
      highlight("[data-act=aod]");
      await wait(800);
      s.aod = true;
      applySettings();
    },
    "set:page1": async () => openSettings(0),
    "setup:on": async () => {
      setConv("idle");
      s.power = "off";
      show("off", "none");
      await wait(700);
      await press("power", 1000);
      await powerOn("wifi");
    },
    "setup:wifi": async () => {
      s.power = "on";
      iphoneLine(false);
      show("wifi", "none");
    },
    // Android sends the Wi-Fi over Bluetooth with the setup password; an iPhone joins the watch's network
    "setup:android": async () => {
      s.power = "on";
      iphoneLine(false);
      show("wifi", "none");
      highlight(".wi-pass");
    },
    "setup:iphone": async () => {
      s.power = "on";
      iphoneLine(true);
      show("wifi", "none");
      highlight(".wi-qr, .wi-net");
    },
    "setup:pair": async () => {
      s.power = "on";
      show("off", "none");
      await wait(500);
      show("pairing", "none");
    },
    "setup:done": async () => {
      s.power = "on";
      show("watchface", "none");
      await wait(900);
      await press("talk");
      chat.replaceChildren();
      await turn("Hello!", "Hi! I'm olá. Ask me anything, or ask me to note something or remind you.");
    },
  };

  function armDelete() {
    const del = root.querySelector<HTMLElement>("[data-view=note] [data-act=note-del]");
    if (!del) return;
    del.classList.add("armed");
    const live = el("div", "dt-live bad", "Tap again to delete");
    q("[data-note-body] .dt-card").append(live);
  }

  // ---- direct use of the simulated watch ---------------------------------------------------------------
  const DIRECT: Record<string, (arg?: string) => void> = {
    talk: () => void run(talk),
    home: () => void run(async () => home()),
    open: (what) => {
      if (what === "notes") void run(async () => show("notes"));
      else if (what === "reminders") void run(async () => show("reminders"));
      else if (what === "settings") void run(() => openSettings(0));
      else if (what === "wifi") void run(async () => (iphoneLine(false), show("wifi")));
    },
    back: (what) => void run(async () => show(what as View)),
    note: (n) => void run(async () => ((s.note = Number(n)), show("note"))),
    rem: (n) => void run(async () => ((s.rem = Number(n)), show("reminder"))),
    "note-pin": () => void run(async () => ((curNote().pinned = !curNote().pinned), renderNote())),
    "note-del": () => {
      const armed = root.querySelector("[data-act=note-del].armed");
      if (armed) {
        void run(async () => {
          s.notes = s.notes.filter((n) => n.n !== s.note);
          badges();
          show("notes");
        });
      } else
        void run(async () => {
          armDelete();
          await wait(3000);
          renderNote();
        });
    },
    "note-mic": () => {
      const rec = root.querySelector("[data-note-bar] .mic.rec");
      void run(async () => {
        renderNote(-1, rec ? undefined : { help: true });
        noteMic(!rec);
      });
    },
    "rem-mic": () => {
      const rec = root.querySelector("[data-rem-bar] .mic.rec");
      void run(async () => {
        renderRem(rec ? {} : { live: 'Say a change: "at 10:30", "add Ana", "remove the place".' });
        remMic(!rec);
      });
    },
    "rem-check": () => void run(async () => ((curRem().done = !curRem().done), badges(), renderRem())),
    "rem-del": () => void run(async () => show("reminders")),
    theme: () => void run(async () => ((s.theme = s.theme === "midnight" ? "mono" : "midnight"), applySettings())),
    timeout: () => void run(async () => ((s.timeout = (s.timeout + 1) % TIMEOUTS.length), applySettings())),
    aod: () => void run(async () => ((s.aod = !s.aod), applySettings())),
    lang: (l) => void run(async () => ((s.lang = l === "en" ? "en" : "ro"), applySettings())),
    page: (d) => void run(async () => setPage(s.page + Number(d))),
  };

  screen.addEventListener("click", (e) => {
    if (swiped) return;
    const t = (e.target as HTMLElement).closest<HTMLElement>("[data-act]");
    if (s.power !== "on") {
      // a tap on a resting screen only wakes it
      if (s.power === "sleep") void run(async () => wake());
      return;
    }
    if (s.view === "wifi" || s.view === "pairing") {
      void run(async () => home());
      return;
    }
    if (!t) return;
    const [name, arg] = t.dataset.act!.split(":");
    DIRECT[name]?.(arg);
  });
  screen.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && (e.target as HTMLElement).dataset.act) {
      e.preventDefault();
      (e.target as HTMLElement).click();
    }
  });

  // drag on the brightness / volume blocks
  qa("[data-level]").forEach((card) => {
    const k = card.dataset.level as "brightness" | "volume";
    const blocks = card.querySelector<HTMLElement>(".st-blocks")!;
    const setFrom = (x: number) => {
      const r = blocks.getBoundingClientRect();
      const v = Math.round(Math.max(0, Math.min(1, (x - r.left) / r.width)) * 10) * 10;
      s[k] = k === "brightness" ? Math.max(5, v) : v;
      applySettings();
    };
    blocks.addEventListener("pointerdown", (e) => {
      cancel();
      blocks.setPointerCapture(e.pointerId);
      setFrom(e.clientX);
      e.stopPropagation();
    });
    blocks.addEventListener("pointermove", (e) => {
      if (blocks.hasPointerCapture(e.pointerId)) setFrom(e.clientX);
    });
    blocks.addEventListener("keydown", (e) => {
      const d = e.key === "ArrowRight" || e.key === "ArrowUp" ? 10 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -10 : 0;
      if (!d) return;
      e.preventDefault();
      s[k] = Math.max(k === "brightness" ? 5 : 0, Math.min(100, s[k] + d));
      applySettings();
    });
  });

  // swipes: left on the watchface opens settings; in settings left/right change page, right on page 1 goes home
  let sx = 0, sy = 0, swiping = false, swiped = false;
  screen.addEventListener("pointerdown", (e) => {
    sx = e.clientX;
    sy = e.clientY;
    swiped = false;
    swiping = !(e.target as HTMLElement).closest(".st-blocks");
  });
  screen.addEventListener("pointerup", (e) => {
    if (!swiping || s.power !== "on") return;
    const k = Number(screen.style.getPropertyValue("--k")) || 1;
    const dx = (e.clientX - sx) / k, dy = (e.clientY - sy) / k;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy)) return;
    swiped = true;
    if (s.view === "watchface" && dx < 0) void run(() => openSettings(0));
    else if (s.view === "settings") {
      if (dx < 0) void run(async () => setPage(1));
      else if (s.page === 1) void run(async () => setPage(0));
      else void run(async () => home());
    } else if (dx > 0 && ["chat", "notes", "reminders"].includes(s.view)) void run(async () => home());
    else if (dx > 0 && s.view === "note") void run(async () => show("notes"));
    else if (dx > 0 && s.view === "reminder") void run(async () => show("reminders"));
  });

  // side buttons: top = talk; bottom = power (short press: sleep / wake; hold 2 s: off; press while off: on)
  q("[data-hot=talk]").addEventListener("click", () => {
    hint();
    void run(async () => {
      await press("talk", 150);
      await talk();
    });
  });
  const pwr = q("[data-hot=power]");
  let downAt = 0, holdTimer = 0;
  pwr.addEventListener("pointerdown", () => {
    downAt = Date.now();
    pwr.classList.add("is-pressed");
    if (s.power === "on") holdTimer = window.setTimeout(() => void run(powerOff), 2000);
  });
  const release = () => {
    pwr.classList.remove("is-pressed");
    clearTimeout(holdTimer);
  };
  pwr.addEventListener("pointerleave", release);
  pwr.addEventListener("pointerup", () => {
    const held = Date.now() - downAt;
    release();
    hint();
    if (s.power === "off") void run(() => powerOn());
    else if (held < 2000) void run(async () => (s.power === "sleep" ? wake() : sleep()));
  });
  pwr.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    if (s.power === "off") void run(() => powerOn());
    else void run(async () => (s.power === "sleep" ? wake() : sleep()));
  });

  // ---- tabs and steps ----------------------------------------------------------------------------------
  const tabs = qa<HTMLButtonElement>("[role=tab]");
  const panels = qa("[role=tabpanel]");
  let demoToken = 0;

  function selectStep(btn: HTMLElement | null) {
    qa("[data-step]").forEach((b) => b.classList.toggle("is-active", b === btn));
    qa("[data-step]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  }
  // Desktop: the steps scroll inside a box as tall as the watch. The chosen step goes to the top, with half
  // of the step before it still showing (so it can be picked again); room below lets the last one get there.
  const desktop = window.matchMedia("(min-width: 1024px)");
  const PEEK_GAP = 12;
  const stage = q(".sim-stage");
  const tabbar = q("[data-tabbar]");
  function padSteps() {
    // phones and tablets: the box fills the screen under the site header (4rem), the sticky tab row and watch
    root.style.setProperty("--tabbar-h", `${tabbar.offsetHeight}px`);
    const boxH = desktop.matches ? 0 : Math.max(240, window.innerHeight - 64 - tabbar.offsetHeight - stage.offsetHeight - 12);
    qa("[data-steps]").forEach((ol) => {
      ol.style.height = boxH ? `${boxH}px` : "";
      ol.style.paddingBottom = "";
      const last = ol.lastElementChild as HTMLElement | null;
      const prev = last?.previousElementSibling as HTMLElement | null;
      const room = last ? ol.clientHeight - last.offsetHeight - (prev && desktop.matches ? prev.offsetHeight / 2 + PEEK_GAP : 0) - 20 : 0;
      ol.style.paddingBottom = room > 0 ? `${room}px` : "";
    });
  }
  function scrollToStep(btn: HTMLElement) {
    const li = btn.closest("li")!;
    const ol = li.parentElement as HTMLElement;
    if (ol.clientHeight === 0) return;
    const prev = li.previousElementSibling as HTMLElement | null;
    // desktop: half of the step before stays in sight; phones: the chosen step alone at the top (less confusing)
    const top = !prev ? 0 : desktop.matches ? li.offsetTop - prev.offsetHeight / 2 - PEEK_GAP : li.offsetTop - 16;
    ol.scrollTo({ top: Math.max(0, top), behavior: reduced ? "auto" : "smooth" });
  }
  new ResizeObserver(padSteps).observe(root);
  window.addEventListener("resize", padSteps);
  desktop.addEventListener("change", padSteps);

  function playStep(btn: HTMLElement) {
    selectStep(btn);
    scrollToStep(btn);
    hint((btn.dataset.hot as Hot) || undefined);
    const action = ACTIONS[btn.dataset.run!];
    return run(action ?? (async () => undefined));
  }
  // Only the How it works page puts the tab and step in the address (#notes-4); the home page copy does not.
  const links = root.dataset.howsim === "links";
  const setHash = (id: string, step: number) => {
    if (links) history.replaceState?.(null, "", `#${id}${step ? `-${step + 1}` : ""}`);
  };
  // "Play all" runs by itself while the section is on screen: when it scrolls into view and when another
  // tab is chosen. It stops when the section leaves the screen or a hand touches the watch or a step.
  let inView = false;
  function stopDemo() {
    demoToken++;
    qa("[data-demo]").forEach((b) => b.removeAttribute("aria-busy"));
  }
  async function playAll(panel: Element, from = 0) {
    stopDemo();
    const my = demoToken;
    const btns = [panel.querySelector("[data-demo]"), root.querySelector("[data-demo-stage]")];
    const steps = [...panel.querySelectorAll<HTMLElement>("[data-step]")];
    btns.forEach((b) => b?.setAttribute("aria-busy", "true"));
    for (const st of steps.slice(from)) {
      if (my !== demoToken) return;
      await playStep(st);
      if (my !== demoToken) return;
      await new Promise((r) => setTimeout(r, reduced ? 600 : 1800));
    }
    if (my === demoToken) btns.forEach((b) => b?.removeAttribute("aria-busy"));
  }
  const openPanel = () => panels.find((p) => !p.hidden);

  function selectTab(tab: HTMLButtonElement, focus = false, step = 0, byUser = true) {
    stopDemo();
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    });
    panels.forEach((p) => (p.hidden = p.id !== tab.getAttribute("aria-controls")));
    padSteps(); // the panel just shown had no size while hidden
    if (focus) tab.focus();
    // keep the chosen tab visible in the scrolling tab row (phones), without scrolling the page
    const list = tab.parentElement!;
    list.scrollTo({ left: tab.offsetLeft - (list.clientWidth - tab.offsetWidth) / 2, behavior: reduced ? "auto" : "smooth" });
    s = { ...fresh(), theme: s.theme, brightness: s.brightness, volume: s.volume, timeout: s.timeout, aod: s.aod };
    s.view = "message"; // the screen shows whatever the last tab left: force the watchface back
    show("watchface", "none");
    chat.replaceChildren();
    applySettings();
    badges();
    setConv("idle");
    const steps = root.querySelectorAll<HTMLElement>(`#${tab.getAttribute("aria-controls")} [data-step]`);
    const from = Math.max(0, Math.min(step, steps.length - 1));
    if (inView || byUser) void playAll(openPanel()!, from);
    else if (steps[from]) void playStep(steps[from]);
    if (byUser) setHash(tab.id.replace(/^tab-/, ""), step);
  }
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => selectTab(t));
    t.addEventListener("keydown", (e) => {
      const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (d) selectTab(tabs[(i + d + tabs.length) % tabs.length], true);
    });
  });
  qa("[data-step]").forEach((b) =>
    b.addEventListener("click", () => {
      stopDemo();
      void playStep(b);
      const panel = b.closest("[role=tabpanel]")!;
      const n = [...panel.querySelectorAll("[data-step]")].indexOf(b);
      setHash(panel.id.replace(/^panel-/, ""), n);
    }),
  );
  qa("[data-demo]").forEach((b) => b.addEventListener("click", () => void playAll(b.closest("[role=tabpanel]") ?? openPanel()!)));
  // a hand on the screen or the buttons stops the demo
  ws.addEventListener("pointerdown", stopDemo);
  // on screen: play from the step shown (from the start again once the last one was reached); off screen: stop
  new IntersectionObserver(
    ([e]) => {
      inView = e.isIntersecting;
      const panel = openPanel();
      if (!panel) return;
      if (!inView) return stopDemo();
      if (panel.querySelector("[data-demo][aria-busy]")) return;
      const steps = [...panel.querySelectorAll("[data-step]")];
      const at = steps.findIndex((b) => b.classList.contains("is-active"));
      void playAll(panel, at < 0 || at === steps.length - 1 ? 0 : at);
    },
    { threshold: 0.35 },
  ).observe(q(".sim-stage"));

  applySettings();
  badges();
  // a link can open a tab, or one of its steps: /how-it-works/#notes, /how-it-works/#notes-4
  const [, hashTab, hashStep] = (links && /^#([a-z]+)(?:-(\d+))?$/.exec(location.hash)) || [];
  const linked = hashTab ? tabs.find((t) => t.id === `tab-${hashTab}`) : undefined;
  const start = linked ?? tabs.find((t) => t.getAttribute("aria-selected") === "true") ?? tabs[0];
  if (start) selectTab(start, false, hashStep ? Number(hashStep) - 1 : 0, false);
  if (linked) root.scrollIntoView({ block: "start" });
}

document.querySelectorAll<HTMLElement>("[data-howsim]").forEach(setup);
