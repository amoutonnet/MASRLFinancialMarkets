import numpy as np
import random
import sys
import tensorflow as tf
import tensorflow.keras.backend as K
import itertools
from . import utils


DELTA = utils.DELTA
SIGMA = 1
VAR = SIGMA * SIGMA
LOG_SIGMA = np.log(SIGMA)
NPY_SQRT1_2 = 1 / (2**0.5)
NPY_PI = np.pi


class RLAgent():
    def __init__(self, simulation, N, obs_space_shape, gamma, alpha, beta, temp, lambd, epsilon, hidden_conv_layers, hidden_dense_layers, initializer, verbose):
        self.simulation = simulation  # The simulation we're in
        self.obs_space_shape = obs_space_shape  # The shape of the observation space
        self.gamma = gamma  # The discount rate associated with the agent experience
        self.alpha = alpha  # The learning rate for the parameter of the policy network
        self.beta = beta  # The learning rate for the parameter of the value network
        self.temp = temp  # The temperature parameter for the entropy term
        self.lambd = lambd  # The lambda parameter for general advantage estimate
        self.epsilon = epsilon  # Epsilon for the clipped loss of PPO
        self.optimizer_actor = tf.keras.optimizers.Adam(self.alpha)  # The optimizert for the actor
        self.optimizer_critic = tf.keras.optimizers.Adam(self.beta)  # The optimizert for the critic
        self.hidden_conv_layers = hidden_conv_layers  # The characteristics of the hidden convolutionnal layers
        self.hidden_dense_layers = hidden_dense_layers  # The characteristics of the hidden dense layers
        self.initializer = initializer  # The initializer for main part of network
        self.loss_actor = [float('inf')] * N  # The loss of the actor for each agent
        self.loss_critic = [-float('inf')] * N  # The loss of the critic for each agent
        self.memory = list()   # The memory to track trajectories
        self.verbose = verbose   # Whether you want to print things or not
        self.name = None   # Name of the network
        self.N = N   # The number of agent handled by the network

    def get_base_dense(self, input_, agent_name, network_name):
        x = input_
        for id_, d in enumerate(self.hidden_dense_layers):
            x = tf.keras.layers.Dense(d, activation='relu',
                                      kernel_initializer=self.initializer,
                                      bias_initializer=self.initializer,
                                      name='%s_%s_dense_%d' % (agent_name, network_name, id_))(x)
        return x

    def get_base_conv(self, input_, agent_name, network_name):
        x = input_
        for id_, c in enumerate(self.hidden_conv_layers):
            x = tf.keras.layers.Conv1D(filters=c[0], kernel_size=c[1], padding='same', activation='relu',
                                       kernel_initializer=self.initializer,
                                       bias_initializer=self.initializer,
                                       name='%s_%s_conv_%d' % (agent_name, network_name, id_))(x)
        return tf.keras.layers.Flatten(name='%s_%s_flatten' % (agent_name, network_name))(x)

    def build_network(self):
        raise NotImplementedError

    def learn(self):
        raise NotImplementedError

    def get_actions(self, observations, nbs_simple_policy, time_step):
        raise NotImplementedError

    def get_action_simple_policy(self, observation):
        raise NotImplementedError

    def get_advantages(self, critic_values, rewards):
        values = np.empty((len(rewards),))
        for i in range(len(rewards) - 1):
            values[i] = rewards[i] + self.gamma * critic_values[i + 1] - critic_values[i]
        values[-1] = rewards[-1] - critic_values[-1]
        return np.array(list(itertools.accumulate(values[::-1], lambda x, y: x * (self.gamma * self.lambd) + y))[::-1], dtype=np.float32)

    def get_discounted_rewards(self, rewards):
        return np.array(list(itertools.accumulate(rewards[::-1], lambda x, y: x * self.gamma + y))[::-1], dtype=np.float32)

    def print_verbose(self, episode_reward, rolling_score):
        if self.verbose:
            for i in range(self.N):
                print('Current Score ({:3.2f}) Rolling Average ({:3.2f}) \
                    | Actor Loss ({:.4f}) Critic Loss ({:.4f})'.format(episode_reward[i],
                                                                       rolling_score[i],
                                                                       self.loss_actor[i],
                                                                       self.loss_critic[i]
                                                                       ))


class MarketMakerRL(RLAgent):
    def __init__(
        self,
        simulation,
        N,
        obs_space_shape,
        action_space_limits,
        gamma,
        alpha,
        beta,
        temp,
        lambd,
        epsilon,
        hidden_conv_layers=[],
        hidden_dense_layers=[128],
        initializer='random_normal',
        verbose=False,
        simple_policy_dict={
            'option': 1,
            'sin_mean': 100,
            'sin_amplitude': 10,
            'sin_period': 20,
            'random_walk_step': 1,
        }
    ):
        super().__init__(
            simulation,
            N,
            obs_space_shape,
            gamma,
            alpha,
            beta,
            temp,
            lambd,
            epsilon,
            hidden_conv_layers,
            hidden_dense_layers,
            initializer,
            verbose
        )
        self.action_space_lower_limit = action_space_limits[0]
        self.action_space_upper_limit = action_space_limits[1]
        self.name = 'Market Makers'
        self.simple_policy_dict = simple_policy_dict
        self.build_network()

    def build_network(self):
        """
        This function is used to build the neural network of the agent
        """
        obs = tf.keras.Input(shape=self.obs_space_shape, name='market_makers_obs')
        advantages = tf.keras.Input(shape=(1,), name='market_makers_advantages')
        output_conv = self.get_base_conv(obs, 'market_makers', 'common')
        x = self.get_base_dense(output_conv, 'market_makers', 'actor')
        mulogsig = tf.keras.layers.Dense(2, activation='linear',
                                         kernel_initializer=self.initializer,
                                         bias_initializer=self.initializer,
                                         name='market_makers_means')(x)
        self.policy = tf.keras.Model(inputs=obs, outputs=mulogsig, name='Market_Makers_Policy')
        self.actor = tf.keras.Model(inputs=[obs, advantages], outputs=mulogsig, name='Market_Makers_Actor')

        def actor_loss(y_true, y_pred):
            mu = y_pred[:, 0]
            action = y_true
            log_sigma = y_pred[:, 1]
            sigma = K.exp(log_sigma)
            var = K.square(sigma)
            # log_sigma = LOG_SIGMA
            # sigma = SIGMA
            # var = VAR

            def cdf_gauss(a):
                x = a * NPY_SQRT1_2
                z = K.abs(x)
                half_erfc_z = 0.5 * tf.math.erf(z)
                return tf.where(
                    z < NPY_SQRT1_2,
                    0.5 + 0.5 * tf.math.erf(x),
                    tf.where(
                        x > 0,
                        1.0 - half_erfc_z,
                        half_erfc_z
                    )
                )

            def log_cdf_gauss(x):
                def safe_log(x):
                    return K.log(tf.where(x > 0, x, DELTA))

                return tf.where(
                    x > 6,
                    -cdf_gauss(-x),
                    tf.where(
                        x > -14,
                        safe_log(cdf_gauss(x)),
                        -0.5 * K.square(x) - safe_log(-x) - 0.5 * K.log(2 * NPY_PI)
                    )
                )

            log_lik = tf.where(
                action < self.action_space_upper_limit,
                tf.where(
                    action > self.action_space_lower_limit,
                    -0.5 * K.log(2 * NPY_PI) - log_sigma - 0.5 * K.square(action - mu) / var,
                    log_cdf_gauss((self.action_space_lower_limit - mu) / sigma)
                ),
                log_cdf_gauss(-(self.action_space_upper_limit - mu) / sigma)
            )
            old_log_lik = K.stop_gradient(log_lik)
            advantages_with_entropy = advantages - self.temp * old_log_lik
            ratio = K.exp(log_lik - old_log_lik)
            clipped_ratio = K.clip(ratio, 1 - self.epsilon, 1 + self.epsilon)
            return -K.mean(K.minimum(ratio * advantages_with_entropy, clipped_ratio * advantages_with_entropy), keepdims=True) / self.N

        self.actor.compile(loss=actor_loss, optimizer=self.optimizer_actor, experimental_run_tf_function=False)

        x = self.get_base_dense(output_conv, 'market_makers', 'critic')

        values = tf.keras.layers.Dense(1, activation='linear',
                                       kernel_initializer=self.initializer,
                                       bias_initializer=self.initializer,
                                       name='market_makers_values')(x)

        self.critic = tf.keras.Model(inputs=obs, outputs=values, name='Market_Makers_Critic')
        self.critic.compile(loss='mse', optimizer=self.optimizer_critic, experimental_run_tf_function=False)

        if self.verbose:
            utils.print_to_output(title='Market Makers Networks')
            self.actor.summary()
            print()
            self.critic.summary()

    def get_action_simple_policy(self, observation, time_step):
        """This function takes as input an observation of one market maker
        and output a mean for the new ask price to be drawn. This ask price will be
        drawn according to a normal distribution of stddev SIGMA=1 and mean what
        this function returns.

        Arguments:
            observation {[np.ndarray]} -- [The market maker observation array described in the report]

        Returns:
            action {[int]} -- [the mean of the ask price to be drawn]
        """
        # print(observation)
        # sys.exit()  # Uncomment this line to stop the program just after the print
        # TO COMPLETE (replace 10)
        if self.simple_policy_dict['option'] == 1:
            action = self.simple_policy_dict['sin_mean'] + self.simple_policy_dict['sin_amplitude'] * np.sin(2 * NPY_PI * time_step / self.simple_policy_dict['sin_period'])
        elif self.simple_policy_dict['option'] == 2:
            if np.random.rand() > 0.5:
                action = observation[0, -1] + self.simple_policy_dict['random_walk_step']
            else:
                action = observation[0, -1] - self.simple_policy_dict['random_walk_step']
        elif self.simple_policy_dict['option'] == 3:
            action = observation[0, -1] + np.random.normal(loc=0, scale=self.simple_policy_dict['brown_scale'])
        else:
            raise NotImplementedError
        return action

    def get_actions(self, observations, nbs_simple_policy, time_step):
        if nbs_simple_policy:
            if nbs_simple_policy < len(observations):
                output = self.policy(observations).numpy()
                actions = np.random.normal(output[:, 0], np.exp(output[:, 1]), len(output))
                simple_policy_actions = np.array([self.get_action_simple_policy(obs, time_step) for obs in observations[-nbs_simple_policy:]], dtype=np.float32)
                actions = np.concatenate((actions, simple_policy_actions))
            else:
                actions = np.array([self.get_action_simple_policy(obs, time_step) for obs in observations], dtype=np.float32)
        else:
            output = self.policy(observations).numpy()
            actions = np.random.normal(output[:, 0], np.exp(output[:, 1]), len(output))
        clipped_actions = np.clip(actions, self.action_space_lower_limit, self.action_space_upper_limit)
        return clipped_actions

    def learn(self, nbs_simple_policy=0, epochs=1, batch_size=8):
        # We retrieve all states, actions and reward the agent got during the episode from the memory
        observations, actions, rewards = map(np.array, zip(*self.memory))
        if observations.shape[1] - nbs_simple_policy > 0:
            for i in range(observations.shape[1] - nbs_simple_policy):
                # We process the states values with the critic network
                critic_values = np.squeeze(self.critic(observations[:, i]).numpy())
                # We get the advantage (difference between the discounted reward and the baseline)
                advantages = self.get_advantages(critic_values, rewards[:, i])
                # We normalize advantages
                advantages = utils.normalize(advantages)
                # We train the actor network
                self.loss_actor[i] = self.actor.train_on_batch([observations[:, i], advantages], actions[:, i])
                # We get discounted rewards
                discounted_rewards = self.get_discounted_rewards(rewards[:, i])
                # We train the critic network
                self.loss_critic[i] = self.critic.train_on_batch(observations[:, i], discounted_rewards)
        self.memory.clear()


class DealerRL(RLAgent):
    def __init__(
        self,
        simulation,
        N,
        obs_space_shape,
        action_space_shape,
        gamma,
        alpha,
        beta,
        temp,
        lambd,
        epsilon,
        hidden_conv_layers=[],
        hidden_dense_layers=[128],
        initializer='random_normal',
        verbose=False,
        simple_policy_dict={
            'option': 1,
            'sell_baseline': 0,
            'buy_baseline': 0,
            'simple_buy_amount': 1,
            'simple_sell_amount': 1
        }
    ):
        super().__init__(
            simulation,
            N,
            obs_space_shape,
            gamma,
            alpha,
            beta,
            temp,
            lambd,
            epsilon,
            hidden_conv_layers,
            hidden_dense_layers,
            initializer,
            verbose
        )
        self.action_space_shape = action_space_shape
        self.max_amount = int((action_space_shape[1] - 1) / 2)
        self.name = 'Dealers'
        self.build_network()
        self.simple_policy_dict = simple_policy_dict

    def build_network(self):
        """
        This function is used to build the neural network of the agent
        """
        obs = tf.keras.Input(shape=self.obs_space_shape, name="dealers_obs")
        advantages = tf.keras.Input(shape=(1,), name="dealers_advantages")
        output_conv = self.get_base_conv(obs, 'dealers', 'common')
        x = self.get_base_dense(output_conv, 'dealers', 'actor')
        probability_distrib_parameters = [
            tf.keras.layers.Dense(self.action_space_shape[1], activation='softmax',
                                  kernel_initializer=self.initializer,
                                  bias_initializer=self.initializer,
                                  name='dealers_probs_%d' % id_)(x)
            for id_ in range(self.action_space_shape[0])
        ]
        self.policy = tf.keras.Model(inputs=obs, outputs=probability_distrib_parameters, name='Dealers_Policy')
        self.actor = tf.keras.Model(inputs=[obs, advantages], outputs=probability_distrib_parameters, name='Dealers_Actor')

        def actor_loss(y_true, y_pred):
            out = K.clip(y_pred, DELTA, 1)
            log_lik = y_true * K.log(out)
            old_log_lik = K.stop_gradient(log_lik)
            advantages_with_entropy = advantages - self.temp * K.sum(old_log_lik, axis=-1)
            ratio = K.sum(K.exp(log_lik - old_log_lik), axis=1)
            clipped_ratio = K.clip(ratio, 1 - self.epsilon, 1 + self.epsilon)
            return -K.mean(K.minimum(ratio * advantages_with_entropy, clipped_ratio * advantages_with_entropy), keepdims=True) / self.N

        self.actor.compile(loss=actor_loss, optimizer=self.optimizer_actor, experimental_run_tf_function=False)

        x = self.get_base_dense(output_conv, 'dealers', 'critic')
        values = tf.keras.layers.Dense(1, activation='linear',
                                       kernel_initializer=self.initializer,
                                       bias_initializer=self.initializer,
                                       name="dealers_values")(x)

        self.critic = tf.keras.Model(inputs=obs, outputs=values, name='Dealers_Critic')
        self.critic.compile(loss='mse', optimizer=self.optimizer_critic, experimental_run_tf_function=False)

        if self.verbose:
            utils.print_to_output(title='Dealers Networks')
            self.actor.summary()
            print()
            self.critic.summary()

    def get_action_simple_policy(self, observation, time_step):
        """This function takes as input an observation of one dealer
        and output an amount to trade between -self.max_amount and
        +self.max_amount FOR EACH COMPANY as an array. The total
        number of companies if self.action_space_shape[0].
        e.g. return [-2,0,2] if you want the dealer to
            • sell 2 stocks of company 0
            • do nothing for company 1
            • buy 2 stocks of company 2

        Arguments:
            observation {[np.ndarray]} -- [The dealer observation array described in the report]
            option -- what kind of simple policy to be used

        Returns:
            action {[int]} -- [an amount to trade between -self.max_amount and +self.max_amount]
        """
        # sys.exit()  # Uncomment this line to stop the program just after the print
        # TO COMPLETE (replace [0]*self.action_space_shape[0])
        # simple policy 1: when price increases, buy it, vice versa.
        if self.simple_policy_dict['option'] == 1:
            ask_prices_now = observation[:, -1][:2 * self.simulation.env.Nm:2]
            ask_prices_past = observation[:, -2][:2 * self.simulation.env.Nm:2]
            diff = ask_prices_now - ask_prices_past
            action = np.array([self.simple_policy_dict['simple_buy_amount'] if i > 0 else -self.simple_policy_dict['simple_sell_amount'] for i in diff])
        # simple policy 2: for each dealer, if stock's price is higher than dealer's baseline, sell it, vice versa.
        elif self.simple_policy_dict['option'] == 2:
            ask_prices_now = observation[:, -1][:2 * self.simulation.env.Nm:2]
            compare_to_sellbs = ask_prices_now > self.simple_policy_dict['sell_baseline']
            compare_to_buybs = ask_prices_now < self.simple_policy_dict['buy_baseline']
            action = np.empty((len(compare_to_sellbs),))
            for idx, (i, j) in enumerate(zip(compare_to_sellbs, compare_to_buybs)):
                if i:
                    action[idx] = -self.simple_policy_dict['simple_sell_amount']
                elif j:
                    action[idx] = self.simple_policy_dict['simple_buy_amount']
                else:
                    action[idx] = 0

        # simple policy 3: when price > moving average of past few days, comparing its ratio with threshold of this dealer, to buy or sell shares
        elif self.simple_policy_dict['option'] == 3:
            ask_prices_now = observation[:, -1][:2 * self.simulation.env.Nm:2]
            moving_average = np.mean(observation[:2 * self.simulation.env.Nm:2, :], axis=1)
            diff = ask_prices_now - moving_average
            action = np.array([self.simple_policy_dict['simple_buy_amount'] if i > 0 else -self.simple_policy_dict['simple_sell_amount'] for i in diff])
        elif self.simple_policy_dict['option'] == 4:
            p = np.array([[1 / (2 * self.max_amount + 1)] * (2 * self.max_amount + 1)] * self.simulation.env.Nm)
            c = p.cumsum(axis=1)
            u = np.random.rand(len(c), 1)
            action = (u < c).argmax(axis=1) - self.max_amount
        else:
            raise NotImplementedError

        assert(all([act <= self.max_amount and act >= -self.max_amount for act in action]))
        return action

    def get_actions(self, observations, nbs_simple_policy, time_step):
        if nbs_simple_policy:
            if nbs_simple_policy < len(observations):
                nn_output = self.policy(observations[:-nbs_simple_policy])
                if isinstance(nn_output, list):
                    probabilities = tf.stack(nn_output, axis=1).numpy()
                else:
                    probabilities = tf.expand_dims(nn_output, axis=1).numpy()
                cumsums = np.cumsum(probabilities, axis=-1)
                unif_draws = np.random.rand(*cumsums.shape)
                actions = (unif_draws < cumsums).argmax(axis=-1) - self.max_amount
                simple_policy_actions = np.array([self.get_action_simple_policy(obs, time_step) for obs in observations[-nbs_simple_policy:]], dtype=np.int32)
                return np.vstack((actions, simple_policy_actions))
            else:
                return np.array([self.get_action_simple_policy(obs, time_step) for obs in observations], dtype=np.int32)
        else:
            nn_output = self.policy(observations)
            if isinstance(nn_output, list):
                probabilities = tf.stack(nn_output, axis=1).numpy()
            else:
                probabilities = tf.expand_dims(nn_output, axis=1).numpy()
            cumsums = np.cumsum(probabilities, axis=-1)
            unif_draws = np.random.rand(*cumsums.shape)
            actions = (unif_draws < cumsums).argmax(axis=-1)
            return actions - self.max_amount

    def learn(self, nbs_simple_policy=0, epochs=1, batch_size=8):
        # We retrieve all states, actions and reward the agent got during the episode from the memory
        observations, actions, rewards = map(np.array, zip(*self.memory))
        if observations.shape[1] - nbs_simple_policy > 0:
            # We rescale actions
            actions += self.max_amount
            for i in range(observations.shape[1] - nbs_simple_policy):
                # We process the states values with the critic network
                critic_values = np.squeeze(self.critic(observations[:, i]).numpy())
                # We one-hot encode actions
                actions_list = [tf.keras.utils.to_categorical(actions[:, i, id_], num_classes=self.action_space_shape[1]) for id_ in range(self.action_space_shape[0])]
                # We get the advantage (difference between the discounted reward and the baseline)
                advantages = self.get_advantages(critic_values, rewards[:, i])
                # We normalize advantages
                advantages = utils.normalize(advantages)
                # We train the actor network
                loss = self.actor.train_on_batch([observations[:, i], advantages], actions_list)
                if isinstance(loss, list):
                    self.loss_actor[i] = loss[0]
                else:
                    self.loss_actor[i] = loss
                # We get discounted rewards
                discounted_rewards = self.get_discounted_rewards(rewards[:, i])
                # We train the critic network
                self.loss_critic[i] = self.critic.train_on_batch(observations[:, i], discounted_rewards)
        self.memory.clear()
