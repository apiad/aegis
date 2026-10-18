import { StreamLanguage } from "@codemirror/language";
import { r } from "@codemirror/legacy-modes/mode/r";
export const lang = () => StreamLanguage.define(r);
