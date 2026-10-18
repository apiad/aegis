import { StreamLanguage } from "@codemirror/language";
import { ruby } from "@codemirror/legacy-modes/mode/ruby";
export const lang = () => StreamLanguage.define(ruby);
