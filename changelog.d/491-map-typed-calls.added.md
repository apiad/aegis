- **`aegis map` follows a call through the receiver's type.** A call such as
  `self.queues.turn_ended()` resolved by method name only, so it pointed at
  the wrong definition, often the caller itself, and a call through a protocol
  never reached the class implementing it. The map now types a receiver from
  annotations, constructor calls, class fields, aliased imports and the
  assignments to an attribute elsewhere in the repository, and a call through
  a base method reaches every override. A call on an unknown receiver no
  longer matches module-level functions, and a call on an outside type
  matches nothing. Python, JavaScript and TypeScript; a `Spec` per language
  adds another.
help: code-map#callers-and-impact
