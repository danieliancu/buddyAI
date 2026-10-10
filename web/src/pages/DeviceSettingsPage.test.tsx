import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DeviceSettings, Options } from "../api";
import DeviceSettingsPage from "./DeviceSettingsPage";

const settings: DeviceSettings = {
  language: "ro",
  preferred_language: "ro",
  volume: 70,
  brightness: 80,
  screen_timeout_s: 15,
  timezone: "Europe/London",
  theme: { preset: "midnight", accent: "#4F8CFF", background: "#000000", clock: "#FFFFFF", text: "#FFFFFF" },
  max_listen_s: 30,
  wait_for_speech_s: 20,
  persona_id: null,
  custom_instructions: "",
  llm_model: null,
  tts_voice: null,
  tts_voice_overrides: {},
  speech_rate: 1,
  vad_sensitivity: "medium",
  max_reply_chars: 400,
  history_turns: 6,
  web_search: true,
};

const options: Options = {
  llm_models: [],
  tts: { voices: ["marin"], default_voice: "marin", by_language: {} },
  theme_presets: {},
  languages: [{ code: "ro", name: "Romanian", native_name: "Română", script: "latin", rtl: false, captions: true }],
  vad_sensitivity: ["low", "medium", "high"],
};

const me = vi.hoisted(() => ({
  devices: { list: vi.fn(), settings: vi.fn(), patchSettings: vi.fn() },
  options: vi.fn(),
  personas: { list: vi.fn() },
}));
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return {
    ...real,
    api: {
      ...real.api,
      me: { ...real.api.me, options: me.options, devices: { ...real.api.me.devices, ...me.devices }, personas: { ...real.api.me.personas, ...me.personas } },
    },
  };
});

// jsdom has no ResizeObserver (the watch preview uses one) and no canvas.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext;

beforeEach(() => {
  me.devices.list.mockResolvedValue([
    { id: "w1", name: "My watch", hw_model: "", fw_version: "", paired_at: null, last_seen_at: null, last_ip: null, battery_pct: null, charging: null, rssi: null, online: false, state: null },
  ]);
  me.devices.settings.mockResolvedValue({ settings, version: 1 });
  me.options.mockResolvedValue(options);
  me.personas.list.mockResolvedValue([]);
});

describe("customer watch settings: pause before olá answers", () => {
  it("offers Short / Normal / Long (the VAD sensitivity) with Normal by default", async () => {
    render(
      <MemoryRouter initialEntries={["/my/watches/w1"]}>
        <Routes>
          <Route path="/my/watches/:id" element={<DeviceSettingsPage mode="customer" />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Pause before olá answers")).toBeTruthy();
    const chips = ["Short", "Normal", "Long"].map((name) => screen.getByRole("button", { name }));
    expect(chips[1].className).toMatch(/font-medium/); // Normal = medium is the active one
    expect(screen.queryByText("VAD sensitivity")).toBeNull();
    fireEvent.click(chips[2]);
    expect(chips[2].className).toMatch(/font-medium/); // Long = low
  });
});
