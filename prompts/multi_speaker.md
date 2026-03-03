## Multiple Speaker Format
- In some cases, the transcription will incorrectly assume the speaker has changed when it clearly hasn't, creating many lines of few words. In these cases, you can simply delete the new speaker indication altogether when it seems out of place. For example, "**A**: What do — [new line] **B**: You think? [new line] **A**: About this?" should become "**A**: What do you think about this?". Only do this if it makes obvious sense to do so.
- You will receive one (potentially long) line of text per speaker. If a new topic begins, add a full line break (leaving a blank line) to start a new paragraph.
- Each new line begins with a timestamp, followed by a speaker label in bold, followed by a colon, followed by the text. Please always delete the timestamp, so the line begins with the speaker label.
- Speaker labels are letters given in bold, like **A**:. Maintain all speaker labels *exactly* as provided, even if you can infer the true name of the speaker. Don't add in any brackets or extra whitespace. The colon should always remain outside the speaker name, i.e. **A**: not **A:**.
- Each new speaker must always begin on a new line, separated by a blank line (but remember to always delete the timestamp, so the line begins with the speaker label).
- Don't add bolded text anywhere outside of the speaker labels (this may break important regex parsing).

Return only the improved transcript, maintaining the same format with **Speaker**: text structure (no timestamps).