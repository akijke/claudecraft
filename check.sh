#!/usr/bin/env bash
# Local check of everything: build, Python tests (bridge + addons), live tests in the game.
# Run from the repository root:  bash check.sh        (JAVA_HOME must point at JDK 25)
# The two live tests open the game for a minute or so each, with the master volume at 0.
set -u
cd "$(dirname "$0")"
PY=run/config/claude-chat/runtime/venv/Scripts/python.exe
[ -x "$PY" ] || PY=python
fail=0
run() { echo; echo "== $1"; shift; "$@" || fail=1; }

run "build (all mods)" ./gradlew build -q
run "bridge tests" bash -c "cd bridge && \"$OLDPWD/$PY\" -m unittest 2>&1 | tail -3"
for a in codexcraft geminicraft; do
    run "$a tests" bash -c "cd $a && \"$OLDPWD/$PY\" -m unittest discover -s tests 2>&1 | tail -2"
done
run "live test: Claudecraft + addons in one game" ./gradlew runLiveTest -q
sed -n 3p run/livetest/report.md; grep "✖" run/livetest/report.md && fail=1
run "live test: Overlaycraft alone" ./gradlew :overlaycraft:runLiveTest -q
head -1 overlaycraft/run/livetest/overlay-report.md; grep "✖" overlaycraft/run/livetest/overlay-report.md && fail=1

echo; [ $fail = 0 ] && echo "ALL OK" || echo "SOMETHING FAILED (see above)"
exit $fail
