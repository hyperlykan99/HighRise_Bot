# Emote Permission Checklist

## Everyone / Public (no role required)

### Basic emotes
- [ ] Plain emote name plays emote
- [ ] `!emotes` lists all emotes
- [ ] `!emoteinfo <name>` shows details in emoji format:
  - 🎭 Emote: alias(es)
  - 🆔 ID: emote-...
  - ⏱️ Time: Ns
  - 📌 Type: player / bot / both / none
  - 💾 Source: registry
  - 🗂️ Cat: uncategorized (or category)  ✅ Exists: yes
- [ ] `!loopemote <name>` loops an emote

### Custom loops (all players)
- [ ] Normal player can use `!customemote e1 e2 e3`
- [ ] Normal player can use `!customtimed e1 5 e2 10`
- [ ] Normal player can use `!stopcustom`
- [ ] Normal player can use `!savecustom <pack> e1 e2`
- [ ] Normal player can use `!savecustomtimed <pack> e1 5 e2 10`
- [ ] Normal player can use `!playcustom <pack>`
- [ ] Normal player can use `!custompacks`
- [ ] Normal player can use `!custominfo <pack>`
- [ ] Normal player can use `!renamecustom <old> <new>`
- [ ] Normal player can use `!deletecustom <pack>`
- [ ] Normal player sees all custom commands in `!customhelp` (no VIP gate)
- [ ] Custom loop persists after bot restart
- [ ] Custom timed loop persists after bot restart
- [ ] Saved custom packs persist after bot restart

### Sync (all players)
- [ ] Normal player: `!sync @user` works
- [ ] Normal player: `!syncstop` stops own sync only
- [ ] Normal player: `!syncstop all` is blocked (❌ Only owner/admin can stop everyone's sync.)
- [ ] Normal player: `!syncstatus` works
- [ ] Normal player: `!sync all` is blocked (❌ Only owner/admin can sync everyone.)
- [ ] Normal player: `!sync all @user` is blocked
- [ ] Normal player: `!synchelp` does NOT show `!sync all` or `!syncstop all`
- [ ] Sync follows target's custom loops
- [ ] Sync follows target's bot emotes
- [ ] Sync follows target's dancefloor moves
- [ ] Movement stops custom loop — does NOT stop sync itself

### Hearts (all players)
- [ ] Normal player: `!heart @user` (1 heart) works

### Help commands (all players)
- [ ] `!emotehelp` visible to all — shows sections: 🎭 / 💾 Custom Loops / 🔁 Sync 💖 Hearts
- [ ] `!emotehelp` non-VIP non-staff: shows "⭐ VIP Social Emotes / 🔒 VIP+ required"
- [ ] `!emotehelp` does NOT show 🛡️ Staff Emotes section to non-staff
- [ ] `!customhelp` visible to all (no VIP gate)
- [ ] `!synchelp` visible to all
- [ ] `!socialhelp` non-VIP non-staff: shows "🔒 Social emotes are VIP+ only."
- [ ] `!socialhelp` does NOT show full social list to non-VIP
- [ ] `!dancefloorhelp` — blocked for non-staff (❌ Dancefloor commands are for staff only.)
- [ ] `!botemotehelp` — blocked for non-staff (❌ Bot emote commands are for staff only.)

### Commands browser (all players)
- [ ] `!commands emotes` shows 🎭 Emote menu to all
- [ ] `!commands emote` (alias) also works
- [ ] `!commands emotes` does NOT show 🛡️ Staff Emotes section to non-staff
- [ ] `!commands` main menu shows `!commands emotes` in "More:" section
- [ ] `!commands search emote` returns emote-related results
- [ ] `!commands search sync` returns sync results
- [ ] `!commands search custom` returns custom loop results
- [ ] `!commands search heart` returns heart results
- [ ] `!commands search social` returns social results
- [ ] `!commands search dancefloor` returns dancefloor (staff-gated, hidden for non-staff)
- [ ] `!commands search botemote` returns botemote (staff-gated, hidden for non-staff)
- [ ] `!command emotehelp` returns detail entry
- [ ] `!command emoteinfo` returns detail entry
- [ ] `!command customemote` returns detail entry
- [ ] `!command customtimed` returns detail entry
- [ ] `!command customhelp` returns detail entry
- [ ] `!command sync` returns detail entry
- [ ] `!command synchelp` returns detail entry
- [ ] `!command heart` returns detail entry
- [ ] `!command social` returns detail entry
- [ ] `!command socialhelp` returns detail entry

---

## VIP+ required

- [ ] VIP: `!sync @user` works
- [ ] VIP: `!syncstop` stops own sync only
- [ ] VIP: `!syncstop all` is blocked
- [ ] VIP: `!sync all` is blocked
- [ ] VIP: `!sync all @user` is blocked
- [ ] VIP: `!synchelp` does NOT show `!sync all` or `!syncstop all`
- [ ] VIP: `!socialhelp` shows full social list (💞 VIP+ Socials + 💖 Hearts sections)
- [ ] VIP: `!emotehelp` shows ⭐ VIP Socials section (not the 🔒 lock line)
- [ ] VIP: `!dancefloorhelp` is still blocked (VIP ≠ staff)
- [ ] VIP: `!botemotehelp` is still blocked (VIP ≠ staff)
- [ ] VIP: `!setemote` is still blocked (VIP ≠ staff)
- [ ] VIP: all custom commands still work
- [ ] VIP: `!heart @user <N>` burst (N > 1) works
- [ ] VIP: all social commands work:
  - [ ] `!kiss @user`
  - [ ] `!slap @user`
  - [ ] `!bonk @user`
  - [ ] `!superpunch @user`
  - [ ] `!punch @user`
  - [ ] `!yeet @user`
  - [ ] `!hypnotize @user`
  - [ ] `!duel @user`
  - [ ] `!kicksocial @user`

---

## Staff / Admin / Owner

### Emote admin
- [ ] `!emotehelp` shows all sections including 🛡️ Staff Emotes
- [ ] `!socialhelp` shows full list + 🛡️ Staff Hearts section
- [ ] `!setemote <name> time <sec>` works — responds "✅ Updated … time to Xs permanently."
- [ ] `!syncpersist on|off` works
- [ ] `!botemotehelp` shows full bot emote reference
- [ ] `!botemote @bot <emote>` works
- [ ] `!botemote stop @bot` works
- [ ] `!botemotes` lists bot emotes
- [ ] Bot emotes persist after restart
- [ ] `!commands emotes` shows 🛡️ Staff Emotes section
- [ ] `!command setemote` returns detail entry
- [ ] `!command dancefloor` returns detail entry
- [ ] `!command botemote` returns detail entry
- [ ] `!commands search dancefloor` returns result for staff
- [ ] `!commands search botemote` returns result for staff
- [ ] `!commands search setemote` returns result for staff

### Dancefloor (staff)
- [ ] `!dancefloorhelp` shows full help
- [ ] `!dancefloor setpoint 1`
- [ ] `!dancefloor setpoint 2`
- [ ] `!dancefloor save`
- [ ] `!dancefloor emotes <a b c>`
- [ ] `!dancefloor timed <a sec b sec>`
- [ ] `!dancefloor random <N>`
- [ ] `!dancefloor randomtimed <N|all> <sec>`
- [ ] `!dancefloor randomtimed <N|all> <min> <max>`
- [ ] `!dancefloor start`
- [ ] `!dancefloor stop`
- [ ] `!dancefloor status`
- [ ] `!dancefloor debug`
- [ ] `!dancefloor clear`
- [ ] `!dancefloor savepack <name>`
- [ ] `!dancefloor loadpack <name>`
- [ ] `!dancefloor packs`
- [ ] `!dancefloor packinfo <name>`
- [ ] `!dancefloor renamepack <old> <new>`
- [ ] `!dancefloor deletepack <name>`

### Owner/Admin sync commands
- [ ] `!sync all` syncs all non-bot room players to sender
- [ ] `!sync all @user` syncs all non-bot room players to @user
- [ ] `!syncstop all` stops sync for all non-bot room players
- [ ] `!syncstop all` — bots are skipped
- [ ] `!syncstop all` — sync persistence DB records cleared for stopped players
- [ ] `!syncstop all` — custom loops keep running (not stopped)
- [ ] `!syncstop all` — dancefloor keeps running (not stopped)
- [ ] `!syncstop all` — reports count: ✅ Stopped sync for N players. Bots skipped.
- [ ] `!syncstop all` when no one is synced: No synced players found.
- [ ] Bot as `@user` target for `!sync all` is rejected (❌ Bots cannot be sync leaders.)
- [ ] Leader is not added as their own follower
- [ ] `!synchelp` shows `!sync all`, `!sync all @user`, `!syncstop all`, `!syncpersist on|off`
- [ ] Followers added by `!sync all` follow normal emotes
- [ ] Followers added by `!sync all` follow custom loops
- [ ] Followers added by `!sync all` follow dancefloor emotes
- [ ] `!syncstop` still removes only the sender from sync
- [ ] Sync persistence still works if enabled

### Staff socials
- [ ] `!heart all` sends hearts to all in room
- [ ] `!hearts all <N>` sends burst to all
- [ ] `!heart @user 100` visible burst

---

## Emote timing persistence

- [ ] `!setemote justvibing time 12` responds: "✅ Updated justvibing time to 12.0s permanently."
- [ ] After restart: bot loops justvibing at 12s (not original default)
- [ ] DB key `emote_timing_overrides` is updated on `!setemote <e> time <N>`
- [ ] `apply_saved_emote_timings()` startup log appears when overrides exist
- [ ] `!emoteinfo justvibing` shows Time: 12s after `!setemote justvibing time 12`

---

## Normal player must NOT be able to

- [ ] `!setemote` — blocked
- [ ] `!dancefloor` any subcommand — blocked
- [ ] `!dancefloorhelp` — blocked
- [ ] `!botemote` — blocked
- [ ] `!botemotehelp` — blocked
- [ ] `!syncpersist` — blocked
- [ ] `!sync all` — blocked
- [ ] `!sync all @user` — blocked
- [ ] `!syncstop all` — blocked
- [ ] `!heart all` — blocked
- [ ] Social emotes without VIP (kiss, slap, bonk, yeet, superpunch, hypnotize, duel, kicksocial) — blocked

---

## Regression tests

- [ ] Sync followers still follow normal emotes
- [ ] Sync followers still follow custom loops
- [ ] Sync followers still follow dancefloor emotes
- [ ] `!syncstop` (no args) still removes only the sender from sync
- [ ] Sync persistence still works if enabled
- [ ] Dancefloor catch-up sends ONE immediate emote — does NOT lock followers on first emote
- [ ] Central registry timing is source of truth; no duplicate timing system added
- [ ] All chat messages ≤249 chars in every new handler
- [ ] `!commands` main menu still works unchanged
- [ ] `!commands search <anything>` does not error
