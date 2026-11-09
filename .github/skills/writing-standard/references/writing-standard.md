# Developer documentation writing standard

## Plain sentences

Write so a reader can say the sentence aloud in one breath. Apply this section to plans and chat replies as well as to docs.

- Put the result in the first sentence of a section. Say what the reader will see.
- Use one idea per sentence. If a sentence has more than one clause joined by "and", "so", or a semicolon, split it.
- Use words you would say to a colleague. Then give the file or the field name, on its own, after the point is clear.
- Define a specialist word in the same sentence, the first time you use it. After that, use the same word.
- Keep one rule in one sentence. Put the exception in the next sentence.
- Start a section with the change. A boundary the reader did not ask about can wait until the end of the section, in its own sentence.

Rewrite a draft that does any of these:

- The first line of a section is a file path, a spec number, or a stack of field names.
- One sentence names three or more mechanisms.
- A sentence tells the reader what something is not, before it says what it is.
- A paragraph mixes the result, the rule, the example, and the file path.

Use project-specific style first. Use this standard when the project does not make a different choice.

This standard follows the practical guidance in the [Google developer documentation style guide](https://developers.google.com/style/). Check the current official guide when an exact or changing convention matters.

## Write for the reader's task

State what the page helps the reader understand or accomplish. Put prerequisites before the procedure. Put information in the order the reader needs it.

Prefer one recommended path. Too many alternatives make the reader stop and choose before they understand the system.

## Use direct language

- Address the reader as `you`.
- Use active voice when it names the actor and action clearly.
- Use present tense for current behavior.
- Start instructions with an imperative verb.
- Use one main action in each procedural step.
- Use concrete nouns and verbs.
- Define an unfamiliar term at first use.
- Use one term consistently for the same thing.
- Use American English unless the project specifies another variety.

Do not use `please` in an instruction. Remove promotional claims, conversational filler, exaggerated benefits, and claims that the source cannot support.

Use `must` for a requirement, `can` for an available action or possibility, and `might` for an uncertain outcome. Use `should` only when it clearly means a recommendation and the recommendation is useful.

## Write useful headings

Use one unique level-one heading on each page. Use sentence case and do not skip heading levels.

For task sections, start with a base-form verb:

- `Install the package`
- `Create an API key`
- `Verify the deployment`

For concept and reference sections, use a descriptive noun phrase:

- `Authentication model`
- `Configuration fields`
- `Known limitations`

Avoid links, code formatting, sequence numbers, and unnecessary punctuation in headings.

## Write procedures

Use a numbered list for a sequence. Introduce the procedure only when the introduction adds context.

In each step:

1. Put the purpose before the action when the purpose helps the reader.
2. Put the location or environment before the action.
3. Put the action before its result or justification.
4. Use `Optional:` at the start of an optional step.
5. Separate independent actions into separate steps.

Use the shortest accessible supported path. Avoid directional terms such as `above`, `below`, or `on the right` when a heading, label, or element name can identify the target.

## Format technical text

Use code font for the following items in ordinary prose:

- filenames and paths;
- commands and flags;
- code elements, methods, classes, fields, and values;
- environment variables;
- HTTP methods and status codes;
- text the reader must enter;
- short command output.

Use fenced code blocks for longer commands, code, configuration, and output. Add a language identifier when known. Introduce the block with a sentence that explains its purpose.

Use uppercase names for placeholders unless the project's interface uses a different placeholder convention. Explain each placeholder near the example.

Do not put a shell prompt in a command that the reader should copy. Do not place secrets, real credentials, or private identifiers in examples.

## Write links

Use short, descriptive link text that makes sense without the surrounding sentence. Prefer the destination page title or a phrase that describes the destination.

Do not use vague link text such as `click here`, `here`, `this page`, or `this document`. Do not use a URL as link text unless the literal URL is the information the reader needs.

Provide essential context on the current page. Use a link for supporting or deeper information. Avoid repeated links to the same destination in a short section.

## Write accessible content

- Use semantic headings, lists, tables, and code blocks.
- Give each image meaningful alternative text. Use empty alternative text only for a truly decorative image.
- Do not use color, position, shape, or an icon alone to convey meaning.
- Use table headers and keep tables simple enough to scan.
- Avoid images of text, code, or terminal output when text can express the same information.
- Explain diagrams in the surrounding text.

## Use notes and warnings carefully

Use a note for information that helps but is not required. Use a warning for a real risk of data loss, security exposure, financial cost, service interruption, or another serious consequence.

Place a warning before the risky action. State the consequence and the condition that causes it. Do not use warnings for ordinary tips.

## Keep facts current

State a version or as-of date when behavior can change and the date helps the reader judge the page. Prefer stable product terms over screenshots of temporary interfaces.

When revising a heading that is a published link target, preserve the old target or update inbound links when the publishing system permits it.
