## Single Speaker Format
- The very first line of the first chunk you receive will begin with a timestamp, followed by a speaker label. Please delete BOTH the timestamp AND the speaker label (since there is only one speaker).
- Break the text into natural paragraphs based on topic changes. Add a blank line between paragraphs.
- Don't add bolded text anywhere (this may break important regex parsing).

Return only the improved transcript as plain paragraphs with no timestamps or speaker labels.