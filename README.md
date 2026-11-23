# Halcyon Timetables & Tools

Staff tools extracted from [halcyon-signin-pwa](https://github.com/AlteredCarduino/halcyon-signin-pwa):

- Timetabled Rooms (+ room bookings)
- Personal / Teaching Staff timetables
- Out of lesson
- Registration Attendance
- School year calendar / Calendar
- Student Emails

## Local run

```bash
cd ~/halcyon-timetables-tools
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp /path/to/signInApp_server/.env .env   # or fill .env.example
uvicorn app:app --reload --host 127.0.0.1 --port 8090
```

Open http://127.0.0.1:8090/timetabled-rooms

## Sync jobs

Same scripts as before (run with `.env` loaded):

- `./run_timetabled_rooms.sh`
- `./run_personal_timetables.sh`
- `./run_registration_wholeyear.sh --sync build-html`

## Status

This repo still shares Supabase / iSAMS credentials with the Sign In PWA.
Sign In routes stay in `halcyon-signin-pwa`; this service is Tools-only.

## Sync from Sign In PWA

Tools pages/modules are periodically copied from `halcyon-signin-pwa` (`signInApp_server/`) so this repo stays current while Sign In remains the live host for now.

Last sync: 2026-11-23 (includes Out of lesson setId fix + restored shared top bar).
