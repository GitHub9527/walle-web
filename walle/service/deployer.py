#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author: wushuiyong
# @Created Time : 日  1/ 1 23:43:12 2017
# @Description:


import time
from datetime import datetime

import os
import pwd
import re
from flask import current_app
from flask_socketio import emit
from walle.model.project import ProjectModel
from walle.model.record import RecordModel
from walle.model.task import TaskModel
from walle.service.code import Code
from walle.service.error import WalleError
from walle.service.utils import color_clean, suffix_format
from walle.service.utils import excludes_format
from walle.service.notice import Notice
from walle.service.waller import Waller
from flask_login import current_user

class Deployer:
    '''
    序列号
    '''
    stage = 'init'

    sequence = 0
    stage_prev_deploy = 'prev_deploy'
    stage_deploy = 'deploy'
    stage_post_deploy = 'post_deploy'

    stage_prev_release = 'prev_release'
    stage_release = 'release'
    stage_post_release = 'post_release'

    task_id = '0'
    user_id = '0'
    taskMdl = None
    TaskRecord = None

    console = False

    version = datetime.now().strftime('%Y%m%d%H%M%S')

    local_codebase, dir_codebase_project, project_name = None, None, None
    dir_release, dir_webroot = None, None

    connections, success, errors = {}, {}, {}
    release_version_tar, release_version = None, None
    local = None

    def __init__(self, task_id=None, project_id=None, console=False):
        self.local_codebase = current_app.config.get('CODE_BASE')
        self.localhost = Waller(host='127.0.0.1')
        self.TaskRecord = RecordModel()

        if task_id:
            self.task_id = task_id
            # task start
            current_app.logger.info(self.task_id)
            self.taskMdl = TaskModel().item(self.task_id)
            self.user_id = self.taskMdl.get('user_id')
            self.servers = self.taskMdl.get('servers_info')
            self.project_info = self.taskMdl.get('project_info')

        if project_id:
            self.project_id = project_id
            self.project_info = ProjectModel(id=project_id).item()
            self.servers = self.project_info['servers_info']

        self.project_name = self.project_info['id']
        self.dir_codebase_project = self.local_codebase + str(self.project_name)

        # self.init_repo()

        # start to deploy
        self.console = console

    def config(self):
        return {'task_id': self.task_id, 'user_id': self.user_id, 'stage': self.stage, 'sequence': self.sequence,
                'console': self.console}

    def start(self):
        RecordModel().query.filter_by(task_id=self.task_id).delete()
        TaskModel().get_by_id(self.task_id).update({'status': TaskModel.status_doing})
        self.taskMdl = TaskModel().item(self.task_id)

    # ===================== fabric ================
    # SocketHandler
    def prev_deploy(self):
        '''
        # TODO
        socketio.sleep(0.001)
        1.代码检出前要做的基础工作
        - 检查 当前用户
        - 检查 python 版本
        - 检查 git 版本
        - 检查 目录是否存在
        - 用户自定义命令

        :return:
        '''
        self.stage = self.stage_prev_deploy
        self.sequence = 1

        # 检查 python 版本
        command = 'python --version'
        result = self.localhost.local(command, wenv=self.config())

        # 检查 git 版本
        command = 'git --version'
        result = self.localhost.local(command, wenv=self.config())

        # 检查 目录是否存在
        self.init_repo()

        # self.init_repo() 函数中已经操作了
        # TODO to be removed
        # command = 'mkdir -p %s' % (self.dir_codebase_project)
        # result = self.localhost.local(command, wenv=self.config())

        # 用户自定义命令
        command = self.project_info['prev_deploy']
        if command:
            current_app.logger.info(command)
            with self.localhost.cd(self.dir_codebase_project):
                result = self.localhost.local(command, wenv=self.config())

    def deploy(self):
        '''
        2.检出代码

        :param project_name:
        :return:
        '''
        self.stage = self.stage_deploy
        self.sequence = 2

        # copy to a local version
        self.release_version = '%s_%s_%s' % (
            self.project_name, self.task_id, time.strftime('%Y%m%d_%H%M%S', time.localtime(time.time())))

        with self.localhost.cd(self.local_codebase):
            command = 'cp -rf %s %s' % (self.dir_codebase_project, self.release_version)
            current_app.logger.info('cd %s  command: %s  ', self.dir_codebase_project, command)

            result = self.localhost.local(command, wenv=self.config())

        # 更新到指定 commit_id
        with self.localhost.cd(self.local_codebase + self.release_version):
            command = 'git reset -q --hard %s' % (self.taskMdl.get('commit_id'))
            result = self.localhost.local(command, wenv=self.config())

            if result.exited != Code.Ok:
                raise WalleError(Code.shell_git_fail, message=result.stdout)

    def post_deploy(self):

        '''
        3.检出代码后要做的任务
        - 用户自定义操作命令
        - 代码编译
        - 清除日志文件及无用文件
        -
        - 压缩打包
        - 传送到版本库 release
        :return:
        '''
        self.stage = self.stage_post_deploy
        self.sequence = 3

        # 用户自定义命令
        command = self.project_info['post_deploy']
        if command:
            with self.localhost.cd(self.local_codebase + self.release_version):
                result = self.localhost.local(command, wenv=self.config())

        # 压缩打包
        # 排除文件发布
        self.release_version_tar = '%s.tgz' % (self.release_version)
        with self.localhost.cd(self.local_codebase):
            excludes = excludes_format(self.project_info['excludes'])
            command = 'tar zcf  %s %s %s' % (self.release_version_tar, excludes, self.release_version)
            result = self.localhost.local(command, wenv=self.config())

        # # 指定文件发布
        # self.release_version_tar = '%s.tgz' % (self.release_version)
        # with self.localhost.cd(self.local_codebase):
        #     excludes = suffix_format(self.dir_codebase_project, self.project_info['excludes'])
        #     command = 'tar zcf  %s %s %s' % (self.release_version_tar, excludes, self.release_version)
        #     result = self.local.run(command, wenv=self.config())

    def prev_release(self, waller):
        '''
        4.部署代码到目标机器前做的任务
        - 检查 webroot 父目录是否存在
        :return:
        '''
        self.stage = self.stage_prev_release
        self.sequence = 4

        # 检查 target_releases 父目录是否存在
        if not os.path.exists(self.project_info['target_releases']):
            command = 'mkdir -p %s' % (self.project_info['target_releases'])
            result = waller.run(command, wenv=self.config())

        # 用户自定义命令
        command = self.project_info['prev_release']
        if command:
            current_app.logger.info(command)
            with waller.cd(self.project_info['target_releases']):
                result = waller.run(command, wenv=self.config())

        # TODO md5
        # 传送到版本库 release
        result = waller.put(self.local_codebase + self.release_version_tar,
                            remote=self.project_info['target_releases'], wenv=self.config())
        current_app.logger.info('command: %s', dir(result))

        # 解压
        self.release_untar(waller)

    def release(self, waller):
        '''
        5.部署代码到目标机器做的任务
        - 打包代码 local
        - scp local => remote
        - 解压 remote
        :return:
        '''
        self.stage = self.stage_release
        self.sequence = 5

        with waller.cd(self.project_info['target_releases']):
            # 1. create a tmp link dir
            current_link_tmp_dir = '%s/current-tmp-%s' % (self.project_info['target_releases'], self.task_id)
            command = 'ln -sfn %s/%s %s' % (
                self.project_info['target_releases'], self.release_version, current_link_tmp_dir)
            result = waller.run(command, wenv=self.config())

            # 2. make a soft link from release to tmp link

            # 3. move tmp link to webroot
            current_link_tmp_dir = '%s/current-tmp-%s' % (self.project_info['target_releases'], self.task_id)
            command = 'mv -fT %s %s' % (current_link_tmp_dir, self.project_info['target_root'])
            result = waller.run(command, wenv=self.config())

    def release_untar(self, waller):
        '''
        解压版本包
        :return:
        '''
        with waller.cd(self.project_info['target_releases']):
            command = 'tar zxf %s' % (self.release_version_tar)
            result = waller.run(command, wenv=self.config())

    def post_release(self, waller):
        '''
        6.部署代码到目标机器后要做的任务
        - 切换软链
        - 重启 nginx
        :return:
        '''
        self.stage = self.stage_post_release
        self.sequence = 6

        # 用户自定义命令
        command = self.project_info['post_release']
        if not command:
            return None

        current_app.logger.info(command)
        with waller.cd(self.project_info['target_root']):
            result = waller.run(command, wenv=self.config())

        # 个性化，用户重启的不一定是NGINX，可能是tomcat, apache, php-fpm等
        # self.post_release_service(waller)

    def post_release_service(self, waller):
        '''
        代码部署完成后,服务启动工作,如: nginx重启
        :param connection:
        :return:
        '''
        with waller.cd(self.project_info['target_root']):
            command = 'sudo service nginx restart'
            result = waller.run(command, wenv=self.config())

    def project_detection(self):
        errors = []
        #  walle user => walle LOCAL_SERVER_USER
        # show ssh_rsa.pub （maybe not necessary）
        # command = 'whoami'
        # current_app.logger.info(command)
        # result = self.localhost.local(command, exception=False, wenv=self.config())
        # if result.failed:
        #     errors.append({
        #         'title': u'本地免密码登录失败',
        #         'why': result.stdout,
        #         'how': u'在宿主机中配置免密码登录，把walle启动用户%s的~/.ssh/ssh_rsa.pub添加到LOCAL_SERVER_USER用户%s的~/.ssh/authorized_keys。了解更多：http://walle-web.io/docs/troubleshooting.html' % (
        #         pwd.getpwuid(os.getuid())[0], current_app.config.get('LOCAL_SERVER_USER')),
        #     })

        # LOCAL_SERVER_USER => git

        # LOCAL_SERVER_USER => target_servers
        for server_info in self.servers:
            waller = Waller(host=server_info['host'], user=server_info['user'], port=server_info['port'])
            result = waller.run('id', exception=False, wenv=self.config())
            if result.failed:
                errors.append({
                    'title': u'远程目标机器免密码登录失败',
                    'why': u'远程目标机器：%s 错误：%s' % (server_info['host'], result.stdout),
                    'how': u'在宿主机中配置免密码登录，把宿主机用户%s的~/.ssh/ssh_rsa.pub添加到远程目标机器用户%s的~/.ssh/authorized_keys。了解更多：http://walle-web.io/docs/troubleshooting.html' % (
                    pwd.getpwuid(os.getuid())[0], server_info['host']),
                })

                # 检查 webroot 父目录是否存在,是否为软链
            command = '[ -L "%s" ] && echo "true" || echo "false"' % (self.project_info['target_root'])
            result = waller.run(command, exception=False, wenv=self.config())
            if result.stdout == 'false':
                errors.append({
                    'title': u'远程目标机器webroot不能是已建好的目录',
                    'why': u'远程目标机器%s webroot不能是已存在的目录，必须为软链接，你不必新建，walle会自行创建。' % (server_info['host']),
                    'how': u'手工删除远程目标机器：%s webroot目录：%s' % (server_info['host'], self.project_info['target_root']),
                })

        # remote release directory
        return errors

    def list_tag(self):
        self.init_repo()

        with self.localhost.cd(self.dir_codebase_project):
            command = 'git tag -l'
            result = self.localhost.local(command, pty=False, wenv=self.config())
            tags = result.stdout.strip()
            tags = tags.split('\n')
            return [color_clean(tag.strip()) for tag in tags]

        return None

    def list_branch(self):
        self.init_repo()

        with self.localhost.cd(self.dir_codebase_project):
            command = 'git pull'
            result = self.localhost.local(command, wenv=self.config())

            if result.exited != Code.Ok:
                raise WalleError(Code.shell_git_pull_fail, message=result.stdout)

            current_app.logger.info(self.dir_codebase_project)

            command = 'git branch -r'
            result = self.localhost.local(command, pty=False, wenv=self.config())

            # if result.exited != Code.Ok:
            #     raise WalleError(Code.shell_run_fail)

            # TODO 三种可能: false, error, success
            branches = result.stdout.strip()
            branches = branches.split('\n')
            # 去除 origin/HEAD -> 当前指向
            # 去除远端前缀
            branches = [branch.strip().lstrip('origin/') for branch in branches if
                        not branch.strip().startswith('origin/HEAD')]
            return branches

        return None

    def list_commit(self, branch):
        self.init_repo()
        with self.localhost.cd(self.dir_codebase_project):
            command = 'git checkout %s && git pull' % (branch)
            self.localhost.local(command, wenv=self.config())

            command = 'git log -50 --pretty="%h #@_@# %an #@_@# %s"'
            result = self.localhost.local(command, pty=False, wenv=self.config())
            current_app.logger.info(result.stdout)

            commit_log = result.stdout.strip()
            current_app.logger.info(commit_log)
            commit_list = commit_log.split('\n')
            commits = []
            for commit in commit_list:
                if not re.search('^.+ #@_@# .+ #@_@# .*$', commit):
                    continue

                commit_dict = commit.split(' #@_@# ')
                current_app.logger.info(commit_dict)
                commits.append({
                    'id': commit_dict[0],
                    'name': commit_dict[1],
                    'message': commit_dict[2],
                })

            return commits

        # TODO
        return None

    def init_repo(self):
        if not os.path.exists(self.dir_codebase_project):
            # 检查 目录是否存在
            command = 'mkdir -p %s' % (self.dir_codebase_project)
            # TODO remove
            current_app.logger.info(command)
            self.localhost.local(command, wenv=self.config())

        with self.localhost.cd(self.dir_codebase_project):
            is_git_dir = self.localhost.local('[ -d ".git" ] && git status', exception=False, wenv=self.config())

        if is_git_dir.exited != Code.Ok:
            # 否则当作新项目检出完整代码
            # 检查 目录是否存在
            command = 'rm -rf %s' % (self.dir_codebase_project)
            self.localhost.local(command, wenv=self.config())

            command = 'git clone %s %s' % (self.project_info['repo_url'], self.dir_codebase_project)
            current_app.logger.info('cd %s  command: %s  ', self.dir_codebase_project, command)

            result = self.localhost.local(command, wenv=self.config())
            if result.exited != Code.Ok:
                raise WalleError(Code.shell_git_init_fail, message=result.stdout)

    def logs(self):
        return RecordModel().fetch(task_id=self.task_id)

    def end(self, success=True, update_status=True):
        if update_status:
            status = TaskModel.status_success if success else TaskModel.status_fail
            current_app.logger.info('success:%s, status:%s' % (success, status))
            TaskModel().get_by_id(self.task_id).update({'status': status})

        notice_info = {
            'title': '',
            'username': current_user.username,
            'project_name': self.project_info['name'],
            'task_name': '%s ([%s](%s))' % (self.taskMdl.get('name'), self.task_id, Notice.task_url(project_name=self.project_info['name'], task_id=self.task_id)),
            'branch': self.taskMdl.get('branch'),
            'commit': self.taskMdl.get('commit_id'),
            'is_branch': self.project_info['repo_mode'],
        }
        notice = Notice.create(self.project_info['notice_type'])
        if success:
            emit('success', {'event': 'finish', 'data': {'message': '部署完成，辛苦了，为你的努力喝彩！'}}, room=self.task_id)
            notice_info['title'] = '上线部署成功'
            notice.deploy_task(project_info=self.project_info, notice_info=notice_info)
        else:
            emit('fail', {'event': 'finish', 'data': {'message': Code.code_msg[Code.deploy_fail]}}, room=self.task_id)
            notice_info['title'] = '上线部署失败'
            notice.deploy_task(project_info=self.project_info, notice_info=notice_info)

    def walle_deploy(self):
        self.start()

        try:
            self.prev_deploy()
            self.deploy()
            self.post_deploy()

            is_all_servers_success = True
            for server_info in self.servers:
                host = server_info['host']
                try:
                    self.connections[host] = Waller(host=host, user=server_info['user'], port=server_info['port'])
                    self.prev_release(self.connections[host])
                    self.release(self.connections[host])
                    self.post_release(self.connections[host])
                except Exception as e:
                    is_all_servers_success = False
                    current_app.logger.error(e)
                    self.errors[host] = e.message
            self.end(is_all_servers_success)

        except Exception as e:
            self.end(False)

        return {'success': self.success, 'errors': self.errors}
