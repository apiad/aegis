import { StreamLanguage } from "@codemirror/language";
import { dart } from "@codemirror/legacy-modes/mode/clike";
export const lang = () => StreamLanguage.define(dart);
