import { StreamLanguage } from "@codemirror/language";
import { csharp } from "@codemirror/legacy-modes/mode/clike";
export const lang = () => StreamLanguage.define(csharp);
