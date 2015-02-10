import logging
import os
import json
import shutil
import requests
from os import listdir
from os.path import join
from os.path import exists
from shutil import rmtree
from functools import partial

from flask import jsonify
from flask import Response
from flask import request

from flask.ext.restful import Resource
from flask.ext.restful import reqparse
from flask.ext.restful import marshal_with
from flask.ext.restful import fields

from application import db
from application import app
from application.utils import list_integers_string

from application.modules.tasks import TaskApi
from application.modules.tasks.model import Task
from application.modules.settings.model import Setting
from application.modules.projects.model import Project
from application.modules.jobs.model import Job
from application.modules.jobs.model import JobManagers
from application.modules.managers.model import Manager

id_list = reqparse.RequestParser()
id_list.add_argument('id', type=str)

status_parser = id_list.copy()
status_parser.add_argument('status', type=str)

job_parser = reqparse.RequestParser()
job_parser.add_argument('project_id', type=int)
job_parser.add_argument('frame_start', type=int)
job_parser.add_argument('frame_end', type=int)
job_parser.add_argument('chunk_size', type=int)
job_parser.add_argument('current_frame', type=int)
job_parser.add_argument('filepath', type=str)
job_parser.add_argument('job_name', type=str)
job_parser.add_argument('render_settings', type=str)
job_parser.add_argument('format', type=str)
job_parser.add_argument('status', type=str)
job_parser.add_argument('priority', type=int)
job_parser.add_argument('managers', type=int, action='append')
job_parser.add_argument('job_type', type=str)

command_parser = reqparse.RequestParser()
command_parser.add_argument('command', type=str)

parser_thumbnail = reqparse.RequestParser()
parser_thumbnail.add_argument("task_id", type=int)


job_fields = {
    'id' : fields.Integer,
    'project_id' : fields.Integer,
    'frame_start' : fields.Integer,
    'frame_end' : fields.Integer,
    'chunk_size' : fields.Integer,
    'current_frame' : fields.Integer,
    'filepath' : fields.String,
    'job_name' : fields.String,
    'render_settings' : fields.String,
    'format' : fields.String,
    'status' : fields.String,
    'priority' : fields.String
}

class JobListApi(Resource):
    def get(self):
        jobs = {}
        for job in Job.query.all():

            tasksforjob = Task.query.filter(Task.job_id == job.id).count()
            taskscompleteforjob = Task.query.filter(Task.job_id == job.id, Task.status == 'finished').count()

            percentage_done = 0

            if tasksforjob and taskscompleteforjob:
                percentage_done = round(float(taskscompleteforjob) / float(tasksforjob) * 100.0, 1)

            remaining_time=None
            average_time=None
            total_time=0
            finished_time=0
            finished_tasks=0
            running_tasks=0
            frames_rendering=""
            frame_remaining=None
            activity=""
            tasks=Task.query.filter(Task.job_id == job.id).all()
            for task in tasks:
                #tasks.append( {'name':task.name, 'status':task.status, 'type':task.type, 'settings':task.settings, 'log':task.log, 'activity':task.activity} )
                try:
                    task_activity=json.loads(task.activity)
                except:
                    task_activity=None


                if task.status=='finished':
                    if task.time_cost:
                        finished_time=finished_time+task.time_cost
                    finished_tasks+=1
                if task.status=='running':
                    running_tasks+=1
                    if task_activity and task_activity.get('current_frame'):
                        frames_rendering="{0} {1}".format(frames_rendering, task_activity.get('current_frame'))
                        if task_activity.get('remaining'):
                            frames_rendering="{0} ({1}sec)".format(frames_rendering, task_activity.get('remaining'))


                if task.time_cost:
                    total_time+=task.time_cost

            if job.status=='running':
                if finished_tasks>0:
                    average_time=finished_time/finished_tasks
                if finished_tasks>0:
                    remaining_time=(average_time*len(tasks))-total_time
                #activity="{0} tasks running".format(running_tasks)
                activity="Rendering: {0}.".format(frames_rendering)

            total_time="{0}sec".format(total_time)

            jobs[job.id] = {"job_name" : job.name,
                            "project_id" : job.project_id,
                            "status" : job.status,
                            "settings" : job.settings,
                            "activity" : activity,
                            "remaining_time" : remaining_time,
                            "average_time" : average_time,
                            "total_time" : total_time,
                            "type" : job.type,
                            "priority" : job.priority,
                            "percentage_done" : percentage_done }

        return jsonify(jobs)

    def start(self, job_id):
        job = Job.query.get(job_id)
        if job:
            if job.status not in ['running', 'completed', 'failed']:
                job.status = 'running'
                db.session.add(job)

                db.session.query(Task).filter(Task.job_id == job_id)\
                        .filter(Task.status == 'aborted')\
                        .update({'status' : 'ready'})
                db.session.commit()
                print ('[debug] Dispatching tasks')
            TaskApi.dispatch_tasks()
        else:
            print('[error] Job %d not found' % job_id)
            raise KeyError

    def stop(self, job_id):
        print '[info] Stopping job', job_id
        # first we delete the associated jobs (no foreign keys)
        job = Job.query.get(job_id)
        if job:
            if job.status not in ['stopped', 'completed', 'failed']:
                TaskApi.stop_tasks(job.id)
                job.status = 'stopped'
                db.session.add(job)
                db.session.commit()
        else:
            print('[error] Job %d not found' % job_id)
            raise KeyError

    def reset(self, job_id):
        job = Job.query.get(job_id)
        if job:
            if job.status == 'running':
                print'Job %d is running' % job_id
                raise KeyError
            else:
                job.status = 'ready'
                db.session.add(job)
                db.session.commit()

                TaskApi.delete_tasks(job.id)
                TaskApi.create_tasks(job)

                path = os.path.join(job.project.render_path_server, str(job.id))
                if os.path.exists(path):
                    rmtree(path)
        else:
            print('[error] Job %d not found' % job_id)
            raise KeyError

    def respawn(self, job_id):
        job = Job.query.get(job_id)
        if job:
            if job.status == 'running':
                self.stop(job_id)

            tasks = db.session.query(Task).filter(Task.job_id == job_id, Task.status.notin_(['finished','failed'])).all()
            best_managers = db.session.query(Manager).join(JobManagers, Manager.id == JobManagers.manager_id)\
                                                    .filter(JobManagers.job_id == job_id)\
                                                    .filter(Manager.has_virtual_workers == 1)\
                                                    .first()

            if best_managers:
                fun = partial(TaskApi.start_task, best_managers)
                map(fun, tasks)
            else:
                map(lambda t : setattr(t, 'status', 'ready'), tasks)
                db.session.commit()
                TaskApi.dispatch_tasks()
        else:
            logging.error('Job %d not found' % job_id)
            raise KeyError


    def put(self):
        args = status_parser.parse_args()
        fun = None
        if args['status'] == "start":
            fun = self.start
        elif args['status'] == "stop":
            fun = self.stop
        elif args['status'] == "reset":
            fun = self.reset
        elif args['status'] == "respawn":
            fun = self.respawn
        else:
            print "command not found"
            return args, 400

        try:
            map(fun, list_integers_string(args['id']))
        except KeyError:
            return args, 404

        args['status'] = 'running'
        return args, 200

    @marshal_with(job_fields)
    def post(self):
        args = job_parser.parse_args()

        job_settings = {
            'frame_start' : args['frame_start'],
            'frame_end' : args['frame_end'],
            'chunk_size' : args['chunk_size'],
            'filepath' : args['filepath'],
            'render_settings' : args['render_settings'],
            'format' : args['format'],
            }

        job = Job(
           project_id=args['project_id'],
           settings=json.dumps(job_settings),
           name=args['job_name'],
           status=args['status'],
           type=args['job_type'],
           priority=args['priority'])

        db.session.add(job)
        db.session.commit()

        allowed_managers = args['managers']
        for m in allowed_managers:
            print "allowed managers: %d" % int(m)
            db.session.add(JobManagers(job_id=job.id, manager_id=int(m)))

        db.session.commit()

        #logging.info('Parsing job to create tasks')
        TaskApi.create_tasks(job)
        #logging.info('Refresh list of available workers')
        #TaskApi.dispatch_tasks()
        return job, 201


class JobApi(Resource):
    @marshal_with(job_fields)
    def get(self, job_id):
        job = Job.query.get_or_404(job_id)
        return job

    @marshal_with(job_fields)
    def put(self, job_id):
        job = Job.query.get_or_404(job_id)
        args = job_parser.parse_args()
        commands = command_parser.parse_args()
        # Here we can handle direct commands for a job, that do not fit the
        # restful principle. For example restart, stop, start, etc.
        if commands['command']:
            if commands['command'] == 'start':
                self.start(job_id)
                return job
            elif commands['command'] == 'stop':
                self.stop(job_id)
                return job # stop job
            elif commands['command'] == 'reset':
                return job # reset job
            else:
                response = jsonify({
                    'code' : 400,
                    'message': 'Unknown command. Try "start", "stop" or "reset"'})
                response.status_code = 400
                return response
        else:
            # We edit properties of the job, such as the title, the frame
            # range and so on
            logging.info('Updating job: {0} - {1}'.format(job.id, job.name))
            for arg in args:
                if args[arg]: setattr(job, arg, args[arg])
            db.session.commit()

            return job

    @staticmethod
    def delete(job_id):
        """Depending on the database, we migh want to specify or not CASCADE
        directives for attached tasks. In order to delete a job, it should
        be not rendering or processing.
        """
        job = Job.query.get_or_404(job_id)
        db.session.delete(job)

        return '', 204

    @staticmethod
    def start(job_id):
        job = Job.query.get(job_id)
        if job.status != 'running':
            job.status = 'running'
            db.session.add(job)
            db.session.commit()
            logging.info('Dispatching tasks')
        else:
            pass
            # TODO (fsiddi): proper error message if jobs is already running
        TaskApi.dispatch_tasks()

    @staticmethod
    def stop(job_id):
        logging.info('Stopping job {0}'.format(job_id))
        job = Job.query.get(job_id)
        if job.status != 'stopped':
            TaskApi.stop_tasks(job.id)
            job.status = 'stopped'
            db.session.add(job)
            db.session.commit()
        else:
            pass
            # TODO (fsiddi): proper error message if jobs is already stopped

    @staticmethod
    def reset(job_id):
        job = Job.query.get(job_id)
        if job.status == 'running':
            logging.error('Job {0} is running'.format(job_id))
            response = jsonify({
                'code' : 400,
                'message': 'This job is running, stop it first.'})
            response.status_code = 400
            return response
        else:
            job.current_frame = job.frame_start
            job.status = 'ready'
            db.session.add(job)
            db.session.commit()

            TaskApi.delete_tasks(job.id)
            TaskApi.create_tasks(job)

            path = os.path.join(job.project.render_path_server, str(job.id))
            if os.path.exists(path):
                rmtree(path)
            logging.info('Job {0} reset end ready'.format(job_id))

class JobDeleteApi(Resource):
    def post(self):
        args = id_list.parse_args()
        print Job.query.all()
        print args['id']
        int_list = list_integers_string(args['id'])
        for j in int_list:
            TaskApi.delete_tasks(j)
            job = Job.query.get(j)
            if job:
                path = os.path.join(job.project.render_path_server, str(j))
                if exists(path):
                    rmtree(path)

                db.session.query(JobManagers).filter(JobManagers.job_id == job.id).delete()
                db.session.delete(job)
                db.session.commit()
                print "[info] Deleted job %d" % j
            else:
                print "[error] Job %d not found" % j
                return '', 404

        return '', 204


class JobThumbnailListApi(Resource):
    """Thumbnail list interface for the Server
    """
    def allowed_file(self, filename):
        """Filter extensions acording to THUMBNAIL_EXTENSIONS configuration.
        """
        return '.' in filename and \
               filename.rsplit('.', 1)[1] in app.config['THUMBNAIL_EXTENSIONS']

    def post(self):
        """Accepts a thumbnail file and a task_id and stores it.
        """
        args = parser_thumbnail.parse_args()
        task = Task.query.get(args['task_id'])
        if not task:
            return
        thumbnail_filename = "thumbnail_%s.png" % task.job_id

        file = request.files['file']
        if file and self.allowed_file(file.filename):
            filepath=os.path.join( app.config['TMP_FOLDER'] , thumbnail_filename)
            filepath_last=os.path.join( app.config['TMP_FOLDER'] , 'thumbnail_0.png')
            file.save(filepath)
            shutil.copy2(filepath, filepath_last)


class JobThumbnailApi(Resource):
    """Thumbnail interface for the Server
    """
    def get(self, job_id):
        """Returns the last thumbnail for the Job, or a blank
        image if none. If job_id is 0 return the global last
        thumbnail.
        """
        def generate():
            filename='thumbnail_%s.png' % job_id
            file_path = os.path.join(app.config['TMP_FOLDER'],filename)
            if os.path.isfile(file_path):
                thumb_file = open(str(file_path), 'r')
                return thumb_file.read()
            else:
                with app.open_resource('static/missing_thumbnail.png') as thumb_file:
                    return thumb_file.read()
            return False
        bin = generate()
        if bin:
            return Response(bin, mimetype='image/png')
        else:
            return '',404