
from sqlalchemy import Column, Integer, String

from core.db.model import Base


class RecordDb(Base):
    __tablename__ = 'record'

    id = Column(Integer, primary_key=True)
    guid = Column(String, index=True)  # 剧集ID
    episode_guid = Column(String)  # 某一集的ID
    time = Column(Integer)  # 视频进度，单位秒
    create_time = Column(Integer)  # 记录创建时间
    playback_speed = Column(Integer)  # 倍速
