-- ============================================================
-- 种子数据: 30 个高频信源，覆盖主要技术栈和官方来源
-- PostgreSQL 版本
-- ============================================================

INSERT INTO sources (
    domain, canonical_url, category, subcategory,
    authority_base, alpha, beta, reputation_score,
    docs_path, release_path, freshness_url, status, verified_by
) VALUES

-- ========== 前端框架 ==========
('react.dev',          'https://react.dev',           'framework', 'frontend',  2, 20, 10, 2.00, '/docs',    '/blog',        'https://react.dev',               'active', 'admin'),
('nextjs.org',         'https://nextjs.org',          'framework', 'frontend',  2, 20, 10, 2.00, '/docs',    '/blog',        'https://nextjs.org/docs',         'active', 'admin'),
('vuejs.org',          'https://vuejs.org',           'framework', 'frontend',  2, 20, 10, 2.00, '/guide',   '/blog',        'https://vuejs.org',               'active', 'admin'),
('svelte.dev',         'https://svelte.dev',          'framework', 'frontend',  2, 20, 10, 2.00, '/docs',    '/blog',        'https://svelte.dev',              'active', 'admin'),
('vitejs.dev',         'https://vitejs.dev',          'framework', 'build',     2, 20, 10, 2.00, '/guide',   '/blog',        'https://vitejs.dev',              'active', 'admin'),
('tailwindcss.com',    'https://tailwindcss.com',     'framework', 'css',       2, 20, 10, 2.00, '/docs',    '/blog',        'https://tailwindcss.com',         'active', 'admin'),

-- ========== 后端框架 / 语言 ==========
('nodejs.org',         'https://nodejs.org',          'language',  'runtime',   2, 20, 10, 2.00, '/docs',    '/blog',        'https://nodejs.org',              'active', 'admin'),
('python.org',         'https://python.org',          'language',  'runtime',   2, 20, 10, 2.00, '/doc',     NULL,           'https://python.org',              'active', 'admin'),
('golang.org',         'https://go.dev',              'language',  'runtime',   2, 20, 10, 2.00, '/doc',     '/blog',        'https://go.dev',                  'active', 'admin'),
('rust-lang.org',      'https://www.rust-lang.org',   'language',  'runtime',   2, 20, 10, 2.00, '/learn',   '/blog',        'https://www.rust-lang.org',       'active', 'admin'),
('docs.djangoproject.com','https://docs.djangoproject.com','framework','backend',2, 20, 10, 2.00, '/en/stable',NULL,        'https://docs.djangoproject.com',  'active', 'admin'),
('fastapi.tiangolo.com','https://fastapi.tiangolo.com','framework','backend',   2, 20, 10, 2.00, NULL,       NULL,          'https://fastapi.tiangolo.com',    'active', 'admin'),
('spring.io',          'https://spring.io',           'framework', 'backend',   2, 20, 10, 2.00, '/docs',    '/blog',        'https://spring.io',               'active', 'admin'),
('laravel.com',        'https://laravel.com',         'framework', 'backend',   2, 20, 10, 2.00, '/docs',    NULL,           'https://laravel.com',             'active', 'admin'),

-- ========== 数据库 / 基础设施 ==========
('postgresql.org',     'https://www.postgresql.org',  'docs',      'database',  2, 20, 10, 2.00, '/docs',    '/about/news',  'https://www.postgresql.org',      'active', 'admin'),
('dev.mysql.com',      'https://dev.mysql.com',       'docs',      'database',  2, 20, 10, 2.00, '/doc',     NULL,           'https://dev.mysql.com',           'active', 'admin'),
('redis.io',           'https://redis.io',            'docs',      'database',  2, 20, 10, 2.00, '/docs',    NULL,           'https://redis.io',                'active', 'admin'),
('kubernetes.io',      'https://kubernetes.io',       'docs',      'infra',     2, 20, 10, 2.00, '/docs',    '/blog',        'https://kubernetes.io',           'active', 'admin'),
('docker.com',         'https://docs.docker.com',     'docs',      'infra',     2, 20, 10, 2.00, NULL,       '/blog',        'https://docs.docker.com',         'active', 'admin'),

-- ========== 包管理器 / 注册表 ==========
('www.npmjs.com',      'https://www.npmjs.com',       'registry',  'package',   2, 20, 10, 2.00, '/docs',    NULL,           'https://www.npmjs.com',           'active', 'admin'),
('pypi.org',           'https://pypi.org',            'registry',  'package',   2, 20, 10, 2.00, NULL,       NULL,           'https://pypi.org',                'active', 'admin'),
('crates.io',          'https://crates.io',           'registry',  'package',   2, 20, 10, 2.00, NULL,       NULL,           'https://crates.io',               'active', 'admin'),

-- ========== 技术新闻 / 社区（中等权威） ==========
('github.com',         'https://github.com',          'registry',  'code',      1, 10, 20, 1.00, NULL,       NULL,           'https://github.com',              'active', 'admin'),
('stackoverflow.com',  'https://stackoverflow.com',   'news',      'community', 1, 10, 20, 1.00, NULL,       NULL,           'https://stackoverflow.com',       'active', 'admin'),
('news.ycombinator.com','https://news.ycombinator.com','news',      'community', 1, 10, 20, 1.00, NULL,       NULL,           'https://news.ycombinator.com',    'active', 'admin'),

-- ========== 标准 / 学术 ==========
('ietf.org',           'https://www.ietf.org',        'policy',    'standard',  2, 20, 10, 2.00, NULL,       NULL,           'https://www.ietf.org',            'active', 'admin'),
('w3.org',             'https://www.w3.org',          'policy',    'standard',  2, 20, 10, 2.00, NULL,       '/blog',        'https://www.w3.org',              'active', 'admin'),
('acm.org',            'https://www.acm.org',         'academic',  'journal',   2, 20, 10, 2.00, NULL,       NULL,           'https://www.acm.org',             'active', 'admin'),

-- ========== 中国平台 ==========
('gitee.com',          'https://gitee.com',           'registry',  'code',      1, 10, 20, 1.00, NULL,       NULL,           'https://gitee.com',               'active', 'admin'),
('cnblogs.com',        'https://www.cnblogs.com',     'news',      'community', 1, 10, 20, 1.00, NULL,       NULL,           'https://www.cnblogs.com',         'active', 'admin');
