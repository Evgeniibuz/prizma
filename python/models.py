"""
Database models using SQLAlchemy ORM.

Base is imported from `database` so the engine and the models share one
metadata object. Adding a new model? Just subclass `Base` here.
"""
import uuid

from sqlalchemy import Column, String, Boolean, Integer, Float, DateTime, Text, ForeignKey, DECIMAL, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from database import Base


class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), unique=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(Text)
    auth_method = Column(String(50), nullable=False, default='email')
    twitter_id = Column(String(255), index=True)
    display_name = Column(String(255))
    avatar_url = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_login = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True)
    extra_data = Column(JSON, default={})


class Portfolio(Base):
    __tablename__ = "portfolios"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Holding(Base):
    __tablename__ = "holdings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id = Column(UUID(as_uuid=True), ForeignKey('portfolios.id', ondelete='CASCADE'), nullable=False, index=True)
    coin_id = Column(String(100), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    amount = Column(DECIMAL(30, 10), nullable=False)
    avg_buy_price = Column(DECIMAL(20, 8))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Watchlist(Base):
    __tablename__ = "watchlists"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    coin_id = Column(String(100), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    coin_id = Column(String(100), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    alert_type = Column(String(50), nullable=False)
    target_value = Column(DECIMAL(20, 8), nullable=False)
    current_value = Column(DECIMAL(20, 8))
    is_triggered = Column(Boolean, default=False)
    triggered_at = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True, index=True)
    notification_sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), index=True)
    email = Column(String(255))
    action_type = Column(String(100), nullable=False, index=True)
    ip_address = Column(String(45))
    user_agent = Column(Text)
    extra_data = Column(JSON, default={})
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ChatHistory(Base):
    __tablename__ = "chat_history"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    context = Column(JSON)
    token_count = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CashtagCache(Base):
    """Cache for Twitter cashtag metrics — reduces API calls.

    `velocity_pct` is a numeric percentage so radar and dashboard can
    actually use it. The old VARCHAR `velocity` column is kept for backward
    compatibility with the existing init.sql.
    """
    __tablename__ = "cashtag_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String(20), nullable=False, unique=True, index=True)
    mentions = Column(Integer, nullable=False, default=0)
    sentiment = Column(Integer, nullable=False, default=50)
    velocity = Column(String(20), nullable=False, default='+0%')  # legacy
    velocity_pct = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Wallet(Base):
    """A Solana wallet linked to a user (Sign-In With Solana).

    Hold-to-access tiers are resolved from the on-chain $PLSX balance of the
    verified wallet — see services/token_balance.py.
    """
    __tablename__ = "wallets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    chain = Column(String(20), nullable=False, default='solana')
    address = Column(String(64), nullable=False, unique=True, index=True)  # base58 pubkey
    verified_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AgentConfig(Base):
    """Per-user dedicated agent — strategy prompt + autonomous schedule (pro tier)."""
    __tablename__ = "agent_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    strategy_prompt = Column(Text, nullable=False, default='')
    watch_symbols = Column(JSON, default=[])
    interval_minutes = Column(Integer, nullable=False, default=360)
    is_active = Column(Boolean, default=False, index=True)
    last_run_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    """A single autonomous (or manual) mission executed by a user's agent."""
    __tablename__ = "agent_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    task = Column(Text, nullable=False)
    result = Column(Text)
    trigger = Column(String(20), nullable=False, default='auto')  # 'auto' | 'manual'
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class Signal(Base):
    """AI-generated trading signal (moved from SQLite to Postgres)."""
    __tablename__ = "signals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action = Column(String(20), nullable=False)
    ticker = Column(String(20), nullable=False, index=True)
    confidence = Column(Integer, nullable=False)
    entry = Column(Float, nullable=False)
    target = Column(Float, nullable=False)
    stop = Column(Float, nullable=False)
    timeframe = Column(String(50), nullable=False)
    reason = Column(Text, nullable=False)
    market_hash = Column(String(32))
    generation_id = Column(UUID(as_uuid=True), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
