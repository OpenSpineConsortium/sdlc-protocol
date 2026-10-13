"""The validation harness (DESIGN.md): the library's own code run against a
model of what GitHub holds, scripted workers and an oracle written from
spec/PROTOCOL.md. A package only so the tests under tests/ can import its
modules; nothing here is vendored, since scripts/vendor.py copies only
sdlc_protocol/."""
