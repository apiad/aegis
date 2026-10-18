import { StreamLanguage } from "@codemirror/language";
import { powerShell } from "@codemirror/legacy-modes/mode/powershell";
export const lang = () => StreamLanguage.define(powerShell);
