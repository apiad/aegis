import { StreamLanguage } from "@codemirror/language";
import { shell } from "@codemirror/legacy-modes/mode/shell";
export const lang = () => StreamLanguage.define(shell);
