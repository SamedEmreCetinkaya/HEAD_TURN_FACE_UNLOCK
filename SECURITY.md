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
- The liveness checks (three-dimensional motion of the 478-point mesh during the turn, the face seen
  and matched by the infrared camera, the infrared reflection, the face's proportions) were measured on
  one person and a simulated flat picture. They raise the cost of photos and screens; they are not a
  certified defence against masks or other presentation attacks.

See the Security section of the [README](README.md) for the protections in place.
