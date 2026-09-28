import { createContext, useContext } from "react";
import type { Area } from "./api";

/** Which role the current part of the UI belongs to ("admin" = operator, "me" = customer). */
export const AreaContext = createContext<Area>("admin");

export const useArea = (): Area => useContext(AreaContext);
