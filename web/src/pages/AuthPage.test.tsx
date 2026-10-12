import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import AuthPage from "./AuthPage";

const auth = vi.hoisted(() => ({ login: vi.fn() }));
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return { ...real, api: { ...real.api, auth: { ...real.api.auth, ...auth } } };
});

describe("operator sign-in", () => {
  it("asks for the two-factor code when the server requires it and sends it", async () => {
    const onDone = vi.fn();
    auth.login
      .mockRejectedValueOnce(new ApiError(401, { mfa_required: true, message: "Enter the code" }))
      .mockResolvedValueOnce({ user: "boss" });
    render(<AuthPage mode="login" onDone={onDone} />);
    expect(screen.queryByLabelText("Two-factor code")).toBeNull();
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "boss" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const code = await screen.findByLabelText("Two-factor code");
    expect(auth.login).toHaveBeenCalledWith("boss", "long-password", undefined);
    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(auth.login).toHaveBeenLastCalledWith("boss", "long-password", "123456"));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("boss"));
  });

  it("a wrong password is not a code prompt", async () => {
    auth.login.mockRejectedValueOnce(new ApiError(401, "invalid credentials"));
    render(<AuthPage mode="login" onDone={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "boss" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Wrong username or password.")).toBeTruthy();
    expect(screen.queryByLabelText("Two-factor code")).toBeNull();
  });
});
