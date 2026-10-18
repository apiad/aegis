import { StreamLanguage } from "@codemirror/language";
import { dockerFile } from "@codemirror/legacy-modes/mode/dockerfile";
export const lang = () => StreamLanguage.define(dockerFile);
