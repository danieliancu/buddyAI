import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { LoginPage } from "./AuthPages";

const me = vi.hoisted(() => ({ login: vi.fn(), get: vi.fn() }));
vi.mock("../../api", async (orig) => {
  const real = await orig<typeof import("../../api")>();
  return { ...real, api: { ...real.api, me: { ...real.api.me, ...me } } };
});

describe("sign in", () => {
  it("shows / hides the password and sends Remember me", async () => {
    me.login.mockResolvedValue({ id: 1 });
    me.get.mockRejectedValue(new Error("not signed in"));
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    const pw = (await screen.findByLabelText("Password")) as HTMLInputElement;
    expect(pw.type).toBe("password");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(pw.type).toBe("text");
    fireEvent.click(screen.getByRole("button", { name: "Hide password" }));
    expect(pw.type).toBe("password");

    const remember = screen.getByRole("checkbox", { name: "Remember me" }) as HTMLInputElement;
    expect(remember.checked).toBe(true);
    fireEvent.click(remember);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "jane@example.com" } });
    fireEvent.change(pw, { target: { value: "correct-horse-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(me.login).toHaveBeenCalledWith("jane@example.com", "correct-horse-1", false));
  });
});
