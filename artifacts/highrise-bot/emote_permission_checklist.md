# Emote Permission Checklist

## Everyone / Public (no role required)

### Basic emotes
- [ ] Plain emote name plays emote
- [ ] `!emotes` lists all emotes
- [ ] `!emoteinfo <name>` shows details
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
- [ ] Normal player sees all custom commands in `!customhelp` (no VIP message)
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
- [ ] `!emotehelp` visible to all
- [ ] `!customhelp` visible to all (no VIP message)
- [ ] `!synchelp` visible to all
- [ ] `!socialhelp` visible to all
- [ ] `!dancefloorhelp` — blocked for non-staff (❌ Dancefloor commands are for staff only.)
- [ ] `!botemotehelp` — blocked for non-staff (❌ Bot emote commands are for staff only.)

---

## VIP+ required

- [ ] VIP: `!sync @user` works
- [ ] VIP: `!syncstop` stops own sync only
- [ ] VIP: `!syncstop all` is blocked
- [ ] VIP: `!sync all` is blocked
- [ ] VIP: `!sync all @user` is blocked
- [ ] VIP: `!synchelp` does NOT show `!sync all` or `!syncstop all`
- [ ] `!kiss @user`
- [ ] `!slap @user`
- [ ] `!bonk @user`
- [ ] `!hypnotize @user`
- [ ] `!kicksocial @user`
- [ ] `!heart @user <N>` (burst, N > 1)

---

## Staff / Manager / Owner only

### Emote admin
- [ ] `!setemote <name> time <sec>`
- [ ] `!syncpersist on|off`
- [ ] `!botemotehelp` shows full help
- [ ] `!botemote @bot <emote>`
- [ ] `!botemote stop @bot`
- [ ] `!botemotes`
- [ ] Bot emotes persist after restart

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
- [ ] `!superpunch @user`
- [ ] `!yeet @user`
- [ ] `!duel @user`
- [ ] `!heart all`
- [ ] `!hearts all <N>`
- [ ] `!heart @user 100`

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
- [ ] `!superpunch`, `!yeet`, `!duel` — blocked

---

## Regression tests

- [ ] Sync followers still follow normal emotes
- [ ] Sync followers still follow custom loops
- [ ] Sync followers still follow dancefloor emotes
- [ ] `!syncstop` (no args) still removes only the sender from sync
- [ ] Sync persistence still works if enabled
- [ ] Dancefloor catch-up sends ONE immediate emote — does NOT lock followers on first emote
