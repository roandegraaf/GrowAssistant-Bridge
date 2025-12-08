# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | Yes                |

## Reporting a Vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Instead, please report them via email to the project maintainers. You should receive a response within 48 hours.

Please include:
- Type of vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

- Acknowledgment: Within 48 hours
- Initial Assessment: Within 1 week
- Fix Development: Within 30 days for critical issues

## Security Hardening Checklist

Before deploying to production:

### Configuration
- [ ] Change default web interface password
- [ ] Generate a unique secret key (auto-generated if empty)
- [ ] Review API endpoint URL

### Network Security
- [ ] Enable SSL/TLS for web interface
- [ ] Configure firewall to restrict access
- [ ] Bind to localhost if only local access needed

### File System Security
- [ ] Set restrictive permissions: `chmod 600 config.yaml`
- [ ] Protect credentials: `chmod 600 data/credentials.json`

### Raspberry Pi Security
- [ ] Change default user password
- [ ] Use SSH keys instead of passwords
- [ ] Keep OS updated

## Security Features

GrowAssistant Bridge includes:

- Rate limiting on authentication endpoints
- Security headers (X-Frame-Options, CSP, etc.)
- Secure session cookies
- Password hashing with scrypt
- Input validation utilities
- Sensitive data masking in logs

## Security Updates

Subscribe to repository releases to receive notifications about security updates.
