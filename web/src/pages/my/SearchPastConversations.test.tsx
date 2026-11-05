import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ConversationSearchChoice } from "../../api";
import SearchPastConversations from "./SearchPastConversations";

const devices = vi.hoisted(() => ({ conversationSearch: vi.fn(), setConversationSearch: vi.fn() }));
vi.mock("../../api", async (orig) => {
  const real = await orig<typeof import("../../api")>();
  return { ...real, api: { ...real.api, me: { ...real.api.me, devices: { ...real.api.me.devices, ...devices } } } };
});

const base: ConversationSearchChoice = { available: true, wearer: null, enabled: false, authorized_at: null, notice_version: "2026-11" };

beforeEach(() => {
  devices.conversationSearch.mockReset();
  devices.setConversationSearch.mockReset();
});

describe("Search Past Conversations (per watch)", () => {
  it("is hidden while the feature is not available for the account", async () => {
    devices.conversationSearch.mockResolvedValue({ ...base, available: false });
    const { container } = render(<SearchPastConversations id="w1" />);
    await waitFor(() => expect(devices.conversationSearch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("asks who wears the watch before offering anything", async () => {
    devices.conversationSearch.mockResolvedValue(base);
    render(<SearchPastConversations id="w1" />);
    expect(await screen.findByText("Who uses this watch?")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("explains the processing and asks the server to switch on for the holder's own watch", async () => {
    devices.conversationSearch.mockResolvedValue({ ...base, wearer: "self" });
    devices.setConversationSearch.mockResolvedValue({ ...base, wearer: "self", enabled: true, authorized_at: "2026-11-01T10:00:00Z" });
    render(<SearchPastConversations id="w1" />);
    const toggle = await screen.findByRole("switch");
    expect(screen.getByText(/sent to our AI provider/)).toBeTruthy();
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(toggle);
    await waitFor(() => expect(devices.setConversationSearch).toHaveBeenCalledWith("w1", "self", true));
    await screen.findByText(/deletes the searchable representations/);
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
  });

  it("switching off says the conversations stay", async () => {
    devices.conversationSearch.mockResolvedValue({ ...base, wearer: "self", enabled: true });
    devices.setConversationSearch.mockResolvedValue({ ...base, wearer: "self", enabled: false });
    render(<SearchPastConversations id="w1" />);
    expect((await screen.findByText(/your conversation history stays/)).textContent).toMatch(/stops searching at once/);
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(devices.setConversationSearch).toHaveBeenCalledWith("w1", "self", false));
    await screen.findByText(/nothing is processed for searching/);
  });

  it("offers no switch for a watch someone else wears, and declaring it switches off", async () => {
    devices.conversationSearch.mockResolvedValue({ ...base, wearer: "self", enabled: true });
    devices.setConversationSearch.mockResolvedValue({ ...base, wearer: "other", enabled: false });
    render(<SearchPastConversations id="w1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Someone else wears this watch" }));
    await waitFor(() => expect(devices.setConversationSearch).toHaveBeenCalledWith("w1", "other", false));
    expect((await screen.findByTestId("wearer-unavailable")).textContent).toMatch(/Not available yet/);
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("shows a refused change and keeps the previous state", async () => {
    devices.conversationSearch.mockResolvedValue({ ...base, wearer: "self" });
    devices.setConversationSearch.mockRejectedValue(new Error("Not available"));
    render(<SearchPastConversations id="w1" />);
    fireEvent.click(await screen.findByRole("switch"));
    await screen.findByText(/Not available/);
    expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
  });
});
