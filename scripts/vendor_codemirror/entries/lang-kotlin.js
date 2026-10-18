import { StreamLanguage } from "@codemirror/language";
import { kotlin } from "@codemirror/legacy-modes/mode/clike";
export const lang = () => StreamLanguage.define(kotlin);
