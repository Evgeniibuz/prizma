-- ══════════════════════════════════════════════════════════
-- PULSΞ Database Schema - PostgreSQL
-- ══════════════════════════════════════════════════════════

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ══════════════════════════════════════════════════════════
-- USERS
-- ══════════════════════════════════════════════════════════
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(50) UNIQUE,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT,
    auth_method VARCHAR(50) NOT NULL DEFAULT 'email', -- 'email', 'twitter', 'google', 'username'
    twitter_id VARCHAR(255),
    display_name VARCHAR(255),
    avatar_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT true,
    extra_data JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_twitter_id ON users(twitter_id);
CREATE INDEX idx_users_created_at ON users(created_at DESC);

-- ══════════════════════════════════════════════════════════
-- PORTFOLIOS
-- ══════════════════════════════════════════════════════════
CREATE TABLE portfolios (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    is_default BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_portfolios_user_id ON portfolios(user_id);

-- ══════════════════════════════════════════════════════════
-- HOLDINGS
-- ══════════════════════════════════════════════════════════
CREATE TABLE holdings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    portfolio_id UUID NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    coin_id VARCHAR(100) NOT NULL, -- coingecko id (bitcoin, ethereum, etc)
    symbol VARCHAR(20) NOT NULL,
    amount DECIMAL(30, 10) NOT NULL,
    avg_buy_price DECIMAL(20, 8),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(portfolio_id, coin_id)
);

CREATE INDEX idx_holdings_portfolio_id ON holdings(portfolio_id);
CREATE INDEX idx_holdings_coin_id ON holdings(coin_id);

-- ══════════════════════════════════════════════════════════
-- WATCHLISTS
-- ══════════════════════════════════════════════════════════
CREATE TABLE watchlists (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    coin_id VARCHAR(100) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, coin_id)
);

CREATE INDEX idx_watchlists_user_id ON watchlists(user_id);
CREATE INDEX idx_watchlists_coin_id ON watchlists(coin_id);

-- ══════════════════════════════════════════════════════════
-- PRICE ALERTS
-- ══════════════════════════════════════════════════════════
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    coin_id VARCHAR(100) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    alert_type VARCHAR(50) NOT NULL, -- 'price_above', 'price_below', 'percent_change'
    target_value DECIMAL(20, 8) NOT NULL,
    current_value DECIMAL(20, 8),
    is_triggered BOOLEAN DEFAULT false,
    triggered_at TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT true,
    notification_sent BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_alerts_user_id ON alerts(user_id);
CREATE INDEX idx_alerts_coin_id ON alerts(coin_id);
CREATE INDEX idx_alerts_is_active ON alerts(is_active) WHERE is_active = true;

-- ══════════════════════════════════════════════════════════
-- SENTIMENT DATA (from Twitter/X analysis)
-- ══════════════════════════════════════════════════════════
CREATE TABLE sentiment_data (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    hour INTEGER NOT NULL CHECK (hour >= 0 AND hour < 24),
    mentions_count INTEGER DEFAULT 0,
    positive_count INTEGER DEFAULT 0,
    negative_count INTEGER DEFAULT 0,
    neutral_count INTEGER DEFAULT 0,
    sentiment_score DECIMAL(5, 2), -- -100 to +100
    engagement_score INTEGER DEFAULT 0,
    top_keywords JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, date, hour)
);

CREATE INDEX idx_sentiment_symbol_date ON sentiment_data(symbol, date DESC);
CREATE INDEX idx_sentiment_score ON sentiment_data(sentiment_score);

-- ══════════════════════════════════════════════════════════
-- ACTIVITY LOGS
-- ══════════════════════════════════════════════════════════
CREATE TABLE activity_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    email VARCHAR(255),
    action_type VARCHAR(100) NOT NULL, -- 'login', 'register', 'chat', 'mission', etc
    ip_address VARCHAR(45),
    user_agent TEXT,
    extra_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_activity_logs_user_id ON activity_logs(user_id);
CREATE INDEX idx_activity_logs_created_at ON activity_logs(created_at DESC);
CREATE INDEX idx_activity_logs_action_type ON activity_logs(action_type);

-- ══════════════════════════════════════════════════════════
-- AI CHAT HISTORY
-- ══════════════════════════════════════════════════════════
CREATE TABLE chat_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID NOT NULL,
    role VARCHAR(20) NOT NULL, -- 'user', 'assistant'
    message TEXT NOT NULL,
    context JSONB,
    token_count INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chat_history_user_id ON chat_history(user_id);
CREATE INDEX idx_chat_history_session_id ON chat_history(session_id, created_at);

-- ══════════════════════════════════════════════════════════
-- FUNCTIONS & TRIGGERS
-- ══════════════════════════════════════════════════════════

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_portfolios_updated_at BEFORE UPDATE ON portfolios
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_holdings_updated_at BEFORE UPDATE ON holdings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ══════════════════════════════════════════════════════════
-- CASHTAG CACHE
-- Cache Twitter cashtag metrics to reduce API calls
-- ══════════════════════════════════════════════════════════
CREATE TABLE cashtag_cache (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    symbol VARCHAR(20) NOT NULL,
    mentions INTEGER NOT NULL DEFAULT 0,
    sentiment INTEGER NOT NULL DEFAULT 50,
    velocity VARCHAR(20) NOT NULL DEFAULT '+0%',          -- legacy display string
    velocity_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,   -- numeric, used by radar
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX idx_cashtag_symbol ON cashtag_cache(symbol);
CREATE INDEX idx_cashtag_updated ON cashtag_cache(updated_at DESC);

CREATE TRIGGER update_cashtag_cache_updated_at BEFORE UPDATE ON cashtag_cache
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ══════════════════════════════════════════════════════════
-- TRADING SIGNALS (AI-generated, moved from SQLite to Postgres)
-- ══════════════════════════════════════════════════════════
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    action VARCHAR(20) NOT NULL,           -- BUY / SELL / HOLD / CLOSE
    ticker VARCHAR(20) NOT NULL,
    confidence INTEGER NOT NULL,
    entry DOUBLE PRECISION NOT NULL,
    target DOUBLE PRECISION NOT NULL,
    stop DOUBLE PRECISION NOT NULL,
    timeframe VARCHAR(50) NOT NULL,
    reason TEXT NOT NULL,
    market_hash VARCHAR(32),
    generation_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_signals_ticker ON signals(ticker);
CREATE INDEX idx_signals_created_at ON signals(created_at DESC);
CREATE INDEX idx_signals_generation_id ON signals(generation_id);

-- ══════════════════════════════════════════════════════════
-- WALLETS (Sign-In With Solana — hold-to-access $PLSX tiers)
-- ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS wallets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chain VARCHAR(20) NOT NULL DEFAULT 'solana',
    address VARCHAR(64) NOT NULL UNIQUE,    -- base58 pubkey
    verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wallets_user_id ON wallets(user_id);
CREATE INDEX IF NOT EXISTS idx_wallets_address ON wallets(address);

-- ══════════════════════════════════════════════════════════
-- DEDICATED AGENT CONFIG (per-user, pro tier)
-- ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS agent_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    strategy_prompt TEXT NOT NULL DEFAULT '',
    watch_symbols JSONB DEFAULT '[]'::jsonb,
    interval_minutes INTEGER NOT NULL DEFAULT 360,
    is_active BOOLEAN DEFAULT false,
    last_run_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_configs_active ON agent_configs(is_active) WHERE is_active = true;

CREATE TRIGGER update_agent_configs_updated_at BEFORE UPDATE ON agent_configs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ══════════════════════════════════════════════════════════
-- AGENT RUNS (autonomous / manual mission history)
-- ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    result TEXT,
    trigger VARCHAR(20) NOT NULL DEFAULT 'auto',  -- 'auto' | 'manual'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_user_id ON agent_runs(user_id, created_at DESC);

-- ══════════════════════════════════════════════════════════
-- INITIAL DATA
-- ══════════════════════════════════════════════════════════
-- (no seed users in production — register via the UI)
