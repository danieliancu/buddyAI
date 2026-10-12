import { createContext, useContext } from "react";
import type { OperatorMe } from "./api";

/** The signed-in operator (null outside the operator area, e.g. in page tests). */
export const OperatorContext = createContext<{ me: OperatorMe | null; refresh: () => void }>({ me: null, refresh: () => undefined });

export const useOperator = () => useContext(OperatorContext);

/** True unless the operator is known to have the "support" role. The server enforces roles; this only hides
 *  controls a support operator cannot use. */
export const useIsAdmin = (): boolean => useContext(OperatorContext).me?.role !== "support";
