import { StreamLanguage } from "@codemirror/language";
import { swift } from "@codemirror/legacy-modes/mode/swift";
export const lang = () => StreamLanguage.define(swift);
