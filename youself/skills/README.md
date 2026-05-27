# YouSelf-platform skills

Папка для **скілів, які ми ставитимемо клієнту в його VM** — деталізовані
how-to для платформенних можливостей, що рідко потрібні (тобто не
виправдовують місце в always-on system prompt / `SOUL.md`).

## Контракт

Один скіл = одна підпапка з `SKILL.md` усередині (Hermes-skill convention):

```
youself/skills/
├── README.md          ← цей файл
└── <skill-name>/
    └── SKILL.md       ← опис скіла + recipes
    └── (optional)     ← допоміжні файли (.sh, .py, тощо)
```

Назва скіла — kebab-case, відображає функцію: `browser-automation`,
`cron-self-schedule`, `wallet-topup-flow`, тощо.

## Як скіли потрапляють у VM

> **Поки що — ніяк.** Папка створена як landing zone під майбутні скіли.
> Wiring у gold image додамо разом із першим реальним скілом.

План на коли з'явиться вміст:

1. У `build-gold-image.sh` додати крок: `rsync /opt/youself/skills/ /var/lib/vz/snippets/youself-skills/` або копія через `qm guest exec`.
2. У `setup-hermes.sh` (виконується на provision) — копія в `/root/.hermes/skills/youself/`.
3. Hermes сам знаходить скіли у `~/.hermes/skills/` через `SKILLS_GUIDANCE` registry.

## Що сюди НЕ йде

* **Always-on capabilities** (file-send, balance check, status) — для них
  є `hermes_cli/default_soul.py:DEFAULT_SOUL_MD`. Скіли — лінива, on-demand
  частина платформи.
* **Hermes-core skills** — це форк Nous Research; їх скіли живуть у
  `hermes-agent-gold-image/skills/` (upstream-зона). Наші — тільки тут.
* **User-installed skills** — це зона юзера в його VM (`~/.hermes/skills/<custom>/`),
  ми її не торкаємо.
