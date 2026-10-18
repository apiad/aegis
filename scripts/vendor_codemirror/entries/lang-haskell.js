import { StreamLanguage } from "@codemirror/language";
import { haskell } from "@codemirror/legacy-modes/mode/haskell";
export const lang = () => StreamLanguage.define(haskell);
