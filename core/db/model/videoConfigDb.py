from sqlalchemy import Column, Integer, String

from core.db.model import Base


class videoConfigList(Base):
    # 跳过片段的数据库
    __tablename__ = 'video_config_list'

    id = Column(Integer, primary_key=True)
    guid = Column(String, index=True)  # 剧集ID
    startTime = Column(Integer)
    endTime = Column(Integer)

    def get_data(self):
        return {
            'startTime': self.startTime,
            'endTime': self.endTime,
        }


class videoConfigUrl(Base):
    # 剧集平台播放链接的数据库
    __tablename__ = 'video_config_url'

    id = Column(Integer, primary_key=True)
    guid = Column(String, index=True)  # 剧集ID
    parent_guid = Column(String, index=True)  # 剧集的母ID，比如说一集电视对应的季度ID
    url = Column(String)

    def get_data(self):
        return {
            'url': self.url,
        }
