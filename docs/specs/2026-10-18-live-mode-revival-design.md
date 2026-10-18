# Live mode revival: current Gemini Live API, dialog under the video, typed questions

Date: 2026-10-18 · ADR: [0130](../adr/0130-gemini-live-v1beta-3-8-and-resumption.md)

## Problem

Live mode (talk to Gemini about the clip you're looking at) was an
experiment people liked, but it stopped working. The transcript was a
three-line strip above the video. People without a mic couldn't use it at
all, and Send frame sat in the header, away from the conversation.

## What changes

1. **Connection.** Token minting and the socket move to v1beta, and the
   default model becomes `gemini-3.8-live`. The setup turns on context
   compression, so sessions with frames are no longer cut at 2 minutes, and
   session resumption, so the browser reconnects when Google ends a
   connection after about 10 minutes. See ADR 0130.
2. **Dialog under the video.** One panel below the player shows the whole
   conversation: your words on the right, Gemini's on the left, notes in the
   middle. It scrolls to the latest line and stays after the chat ends. Earlier
   sessions on the same page stay above a "New session" divider, dimmed.
   Saved transcripts are still listed in the History tab.
3. **Typed questions.** While a session runs, the panel has a text box and
   a Send button. Enter sends. A typed question is a complete turn in the
   same Live session, so talking and typing can be mixed. If the browser has
   no mic or blocks it, the session starts anyway, with a "No microphone
   available" note and a "Typing only" label in the header.
4. **Send frame and End chat** move from the header into the panel, next to
   the text box. Stop is renamed **End chat** and asks for confirmation
   ("End chat?" with Keep talking / End chat) so a stray click doesn't end
   the session. The header keeps only the REC timer (and "Typing only").
5. **Pause sends the frame.** The pause handler (`liveSession.init`) was
   never called, so it was dead code. It is wired up now: pausing the video
   during a session sends the current frame, as the original spec intended.

Out of scope: a text-only reply mode (native-audio models only answer with
audio; the transcript carries the text), and a rate card for Live models.

## Manual acceptance flows

1. Open a clip whose video is ready, open ⋯ and choose **Live**. Allow the
   mic. Within a few seconds you hear a short Czech greeting, and the panel
   under the video shows it as text.
2. Ask a question aloud. Your words appear on the right, then Gemini's
   answer on the left while you hear it.
3. Type a question in the box and press Enter. It appears on the right and
   Gemini answers (voice and text). The box empties.
4. Pause the video on a different shot, or press **Send frame** in the
   panel. The video flashes, and Gemini can describe the new frame when
   asked.
5. Keep the session going past 2 minutes, sending at least one frame. It
   doesn't drop. If you can, keep it past 10 minutes: the dialog carries on
   with no error.
6. Press **End chat** in the panel. A dialog asks "End chat?". Press
   **Keep talking**: nothing changes. Press **End chat** again and confirm:
   the text box goes away, the transcript stays under the video, and the
   session appears in the History tab with a summary.
7. Start Live again on the same page. The earlier lines stay above a "New
   session" divider.
8. Block the mic for the site (or use a machine without one) and start
   Live. The panel says "No microphone available. Type your questions
   below.", the header shows **Typing only**, and typed questions work.
