# VoiceChat 0.4.0

## New
- **Logo**: back to the coral-to-pink tile with a white voice wave, everywhere (window, taskbar, tray,
  installer, shortcuts, download page).
- **Update window**: checking for and installing updates happens in a small separate window, like Discord;
  the main window opens only after it.
- **Desktop shortcut stays put**: the installer creates it once and never rewrites it on updates; Windows is
  told to refresh icons so the new logo still shows.
- **Avatars**: everyone sets their own picture in Settings → Account; a space's picture is set by its creator
  in the space settings. Pictures are shrunk to 256×256 JPEG on the client.
- **Space settings** (space menu → «Настройки пространства»): picture, name, channel names, the best screen
  share quality members may pick, cameras on or off, and the list of members.
- **Moderators**: the creator appoints them. A moderator can do everything the creator can except create
  and delete channels, delete the space, change its picture and appoint moderators. Moderators cannot
  moderate the creator.
- **Wake up**: right-click a person in voice whose microphone or sound is off → «Разбудить». They hear a
  seven-second tune even with the sound off. One cooldown per person for everybody: after anyone wakes
  them, nobody can for a minute (server-enforced).
- **Webcam** in voice channels with a live preview before turning it on (up to 720p30, end-to-end
  encrypted like voice). Cameras are shown in place of the avatar on the stage.
- **Screen share**: choose a whole screen or a single window, with a live preview of what friends will see;
  the mouse pointer is painted into the stream.

## Server
- Migration 0005: `users.avatar`, `users.avatar_version`, `spaces.avatar`, `spaces.avatar_version`,
  `spaces.max_share_quality`, `spaces.camera_allowed`, table `space_moderators`.
- New endpoints: `PATCH /spaces/{id}`, `GET /spaces/{id}/members`, `PUT /spaces/{id}/moderators/{user_id}`,
  `PUT /me/avatar`, `GET /avatars/user/{id}`, `PUT /spaces/{id}/avatar`, `GET /avatars/space/{id}`,
  `POST /voice/{user_id}/wake`.
- Media tokens allow the `camera` source when the space allows cameras.
