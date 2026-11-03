"""Claude Code as the script writer (claude_code.py), without Claude Code: a narration request the app's own writer
code builds (claude._content) read back into its pieces, as the request folder an agent gets is made from them. A
translated comic's pieces ("Piece 3's text, translated into English: ...") stay with their pictures: each was taken
for a new piece with none, and every chapter of a translated comic failed before its agent started."""
import numpy as np

from helpers import App, check, data_folder, done

app = App(data_folder())
from recap import claude, claude_code  # noqa: E402


def picture(h, shade):
    return np.full((h, 400, 3), shade, np.uint8)


tall = picture(6000, 90)
pieces = [{"img": picture(500, 30), "text": "Hey, this won't do!", "translated": True},
          {"img": tall, "text": "", "speakers": "Jinseong"},
          {"img": picture(300, 200), "text": "OYE", "translated": False}]
blocks = claude._content("Chapter 1", pieces, "", None)
read = claude_code._pieces(blocks[1:-1])
check("three pieces, numbered as in the request", sorted(read) == [1, 2, 3], sorted(read))
check("each with its pictures (a tall one's slices too)",
      [len(read[i]["imgs"]) for i in (1, 2, 3)] == [1, claude.piece_images(tall), 1],
      [len(read[i]["imgs"]) for i in sorted(read)])
check("a translated piece keeps its English", any("translated into English" in t and "won't do" in t
                                                  for t in read[1]["lines"]), read[1]["lines"])
check("and the others their text and speaker guesses", any("No text was read" in t for t in read[2]["lines"])
      and any("Speaker guesses" in t for t in read[2]["lines"]) and any("OYE" in t for t in read[3]["lines"]))
check("the pictures come back as sent", read[1]["imgs"][0].shape[:2] == (500, 400)
      and abs(int(read[3]["imgs"][0].mean()) - 200) < 3)
done()
