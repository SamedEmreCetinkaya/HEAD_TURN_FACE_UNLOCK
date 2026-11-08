# Security

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead: the repository's
**Security** tab, then **Report a vulnerability**. Include the version (`head-turn-face-unlock status`),
the distribution and the steps that show the problem.

Anything that lets a face verification succeed for someone who is not enrolled, lets an unprivileged
user talk to the service, reads or changes a template, or keeps a login from falling back to the
password is in scope.

## What the design assumes

- Root is trusted. Anyone with root can change PAM, the templates or the code.
- The person enrolling is the person whose face is enrolled; enrollment asks for the password every time.
- The infrared liveness checks (reflection level, nose reflection, brightness centroid moving with the
  turn) are heuristics measured on one person. They raise the cost of photos and screens; they are not
  a certified defence against masks or other presentation attacks.

See the Security section of the [README](README.md) for the protections in place.
