import { StreamLanguage } from "@codemirror/language";
import { scala } from "@codemirror/legacy-modes/mode/clike";
export const lang = () => StreamLanguage.define(scala);
